"""Backend-neutral acquisition scheduling for state freshness repair."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Literal

from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    ExternalCatPauseBehavior,
    FieldAvailability,
    FieldCapability,
    MeterCoalescingPolicy,
    RadioAcquisitionProfile,
    ReconciliationPriority,
)
from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    FieldChange,
    FieldPath,
    Observation,
    SourceMetadata,
)
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
    "MeterObservationCoalescer",
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
    reasons: tuple[str, ...]
    timeout: float | None
    requested_at_monotonic: float
    deadline_monotonic: float
    external_cat_owner: str | None


@dataclass(frozen=True, slots=True)
class _AcquisitionRequestKey:
    scope: str
    family: str
    receiver_id: str | None
    slot: str | None
    acquisition_method: AcquisitionMethod
    policy: AcquisitionPolicy


@dataclass(frozen=True, slots=True)
class _CadenceState:
    current_cadence_seconds: float
    next_due_monotonic: float


@dataclass(frozen=True, slots=True)
class _PendingCadenceUpdate:
    request_id: str
    semantic_changed: bool


@dataclass(frozen=True, slots=True)
class _PendingMeterSample:
    observation: Observation
    policy: MeterCoalescingPolicy


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
        "_cadence_by_key",
        "_deferred",
        "_external_cat_owner",
        "_external_cat_paused",
        "_external_cat_reason",
        "_next_id",
        "_pending_cadence_by_key",
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
        self._requests_by_key: dict[_AcquisitionRequestKey, AcquisitionRequest] = {}
        self._deferred: dict[_AcquisitionRequestKey, _PendingEnsureFresh] = {}
        self._cadence_by_key: dict[_AcquisitionRequestKey, _CadenceState] = {}
        self._pending_cadence_by_key: dict[
            _AcquisitionRequestKey,
            _PendingCadenceUpdate,
        ] = {}
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

        if self._external_cat_paused:
            queued: list[AcquisitionRequest] = []
            deferred = False
            for key, grouped_paths in self._request_groups(normalized_paths):
                if self._must_defer_for_external_cat(grouped_paths):
                    deferred = True
                    self._defer(
                        key,
                        _PendingEnsureFresh(
                            paths=grouped_paths,
                            max_age=max_age,
                            priority=normalized_priority,
                            reason=reason,
                            reasons=(reason,),
                            timeout=timeout,
                            requested_at_monotonic=now,
                            deadline_monotonic=now + max_age,
                            external_cat_owner=self._external_cat_owner,
                        ),
                    )
                    continue
                queued.extend(
                    self._queue(
                        paths=grouped_paths,
                        max_age=max_age,
                        priority=normalized_priority,
                        reason=reason,
                        timeout=timeout,
                        requested_at=now,
                        external_cat_owner=self._external_cat_owner,
                    )
                )
            if queued:
                return EnsureFreshResult(
                    status=AcquisitionStatus.QUEUED,
                    request=queued[0],
                )
            if deferred:
                return EnsureFreshResult(
                    status=AcquisitionStatus.DEFERRED,
                    message=self._external_cat_reason
                    or "external CAT ownership active",
                )

        queued_requests = self._queue(
            paths=normalized_paths,
            max_age=max_age,
            priority=normalized_priority,
            reason=reason,
            timeout=timeout,
            requested_at=now,
            external_cat_owner=None,
        )
        if not queued_requests:
            return EnsureFreshResult(
                status=AcquisitionStatus.DEFERRED,
                message=self._external_cat_reason or "external CAT ownership active",
            )
        return EnsureFreshResult(
            status=AcquisitionStatus.QUEUED,
            request=queued_requests[0],
        )

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

    def due_requests(self, *, now: float | None = None) -> tuple[AcquisitionRequest, ...]:
        """Queue and return policy-cadence poll requests that are due."""

        timestamp = self._clock.now() if now is None else now
        groups = self._due_poll_groups(timestamp)
        queued: list[AcquisitionRequest] = []
        for key, grouped_paths in groups:
            policy = key.policy
            assert policy.cadence_seconds is not None
            max_age = (
                policy.freshness_ttl_seconds
                if policy.freshness_ttl_seconds is not None
                else policy.cadence_seconds
            )
            if self._external_cat_paused and self._must_defer_for_external_cat(
                grouped_paths
            ):
                self._defer(
                    key,
                    _PendingEnsureFresh(
                        paths=grouped_paths,
                        max_age=max_age,
                        priority=AcquisitionPriority.BACKGROUND,
                        reason="policy-cadence",
                        reasons=("policy-cadence",),
                        timeout=None,
                        requested_at_monotonic=timestamp,
                        deadline_monotonic=timestamp + max_age,
                        external_cat_owner=self._external_cat_owner,
                    ),
                )
                continue
            queued.extend(
                self._queue_grouped(
                    groups=((key, grouped_paths),),
                    max_age=max_age,
                    priority=AcquisitionPriority.BACKGROUND,
                    reason="policy-cadence",
                    timeout=None,
                    requested_at=timestamp,
                    external_cat_owner=self._external_cat_owner,
                )
            )
        return tuple(queued)

    poll_due_requests = due_requests

    def record_acquisition_result(
        self,
        request: AcquisitionRequest,
        change_set: ChangeSet,
    ) -> None:
        """Update adaptive cadence state after a backend acquisition completes."""

        key = _request_key(
            request.paths[0],
            acquisition_method=request.acquisition_method,
            policy=request.policy,
        )
        existing = self._requests_by_key.get(key)
        matched_pending_request = False
        remaining_paths: tuple[FieldPath, ...] = ()
        if existing is not None and existing.id == request.id:
            matched_pending_request = True
            completed_paths = frozenset(request.paths)
            remaining_paths = tuple(
                path for path in existing.paths if path not in completed_paths
            )
            if remaining_paths:
                if remaining_paths != existing.paths:
                    self._requests_by_key[key] = self._replace_request_paths(
                        existing,
                        paths=remaining_paths,
                    )
            else:
                del self._requests_by_key[key]

        base_cadence = request.policy.cadence_seconds
        if base_cadence is None:
            if matched_pending_request and not remaining_paths:
                self._pending_cadence_by_key.pop(key, None)
            return

        requested_paths = frozenset(request.paths)
        semantic_changed = any(
            change.path in requested_paths for change in change_set.changes
        )
        pending_cadence = self._pending_cadence_by_key.get(key)
        if matched_pending_request and remaining_paths:
            if pending_cadence is not None and pending_cadence.request_id == request.id:
                semantic_changed = (
                    semantic_changed or pending_cadence.semantic_changed
                )
            self._pending_cadence_by_key[key] = _PendingCadenceUpdate(
                request_id=request.id,
                semantic_changed=semantic_changed,
            )
            return

        if pending_cadence is not None and pending_cadence.request_id == request.id:
            semantic_changed = semantic_changed or pending_cadence.semantic_changed
            del self._pending_cadence_by_key[key]

        previous = self._cadence_state_for(
            key,
            request.policy,
            now=change_set.timestamp_monotonic,
        )
        if semantic_changed or not request.policy.adaptive_decay.enabled:
            current_cadence = base_cadence
        else:
            current_cadence = (
                previous.current_cadence_seconds
                * request.policy.adaptive_decay.idle_multiplier
            )
            max_cadence = request.policy.adaptive_decay.max_cadence_seconds
            if max_cadence is not None:
                current_cadence = min(current_cadence, max_cadence)
        self._cadence_by_key[key] = _CadenceState(
            current_cadence_seconds=current_cadence,
            next_due_monotonic=change_set.timestamp_monotonic + current_cadence,
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return a JSON-safe scheduler projection for diagnostics surfaces."""

        now = self._clock.now()
        cadence_by_path: dict[str, dict[str, Any]] = {}
        cadence_by_group: dict[str, dict[str, Any]] = {}
        for key, paths in self._poll_cadence_groups().items():
            policy = key.policy
            if policy.cadence_seconds is None:
                continue
            state = self._cadence_state_for(key, policy, now=now)
            group_key = _diagnostic_group_key(key)
            payload = {
                "paths": [str(path) for path in paths],
                "baseCadenceSeconds": policy.cadence_seconds,
                "currentCadenceSeconds": state.current_cadence_seconds,
                "nextDueMonotonic": state.next_due_monotonic,
            }
            cadence_by_group[group_key] = payload
            for path in paths:
                cadence_by_path[str(path)] = {
                    "group": group_key,
                    "baseCadenceSeconds": policy.cadence_seconds,
                    "currentCadenceSeconds": state.current_cadence_seconds,
                    "nextDueMonotonic": state.next_due_monotonic,
                }

        return {
            "queuedRequestCount": len(self._requests_by_key),
            "deferredRequestCount": len(self._deferred),
            "cadenceByPath": cadence_by_path,
            "cadenceByGroup": cadence_by_group,
            "requestPressureByPriorityFamily": self._request_pressure(),
        }

    to_diagnostics = diagnostics

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
        for key, request in tuple(self._requests_by_key.items()):
            if not self._must_defer_for_external_cat(request.paths):
                continue
            del self._requests_by_key[key]
            self._defer(
                key,
                _PendingEnsureFresh(
                    paths=request.paths,
                    max_age=request.max_age,
                    priority=request.priority,
                    reason=request.reason,
                    reasons=request.reasons,
                    timeout=request.timeout,
                    requested_at_monotonic=request.requested_at_monotonic,
                    deadline_monotonic=request.deadline_monotonic,
                    external_cat_owner=owner,
                ),
            )

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
            queued.extend(
                self._queue(
                    paths=item.paths,
                    max_age=item.max_age,
                    priority=item.priority,
                    reason=item.reason,
                    reasons=item.reasons,
                    timeout=item.timeout,
                    requested_at=item.requested_at_monotonic,
                    external_cat_owner=item.external_cat_owner or owner,
                    deadline_monotonic=item.deadline_monotonic,
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
        reasons: tuple[str, ...] | None = None,
        deadline_monotonic: float | None = None,
    ) -> tuple[AcquisitionRequest, ...]:
        request_reasons = (reason,) if reasons is None else reasons
        request_deadline = (
            requested_at + max_age
            if deadline_monotonic is None
            else deadline_monotonic
        )
        return self._queue_grouped(
            groups=self._request_groups(paths),
            max_age=max_age,
            priority=priority,
            reason=reason,
            timeout=timeout,
            requested_at=requested_at,
            external_cat_owner=external_cat_owner,
            reasons=request_reasons,
            deadline_monotonic=request_deadline,
        )

    def _queue_grouped(
        self,
        *,
        groups: tuple[tuple[_AcquisitionRequestKey, tuple[FieldPath, ...]], ...],
        max_age: float,
        priority: AcquisitionPriority,
        reason: str,
        timeout: float | None,
        requested_at: float,
        external_cat_owner: str | None,
        reasons: tuple[str, ...] | None = None,
        deadline_monotonic: float | None = None,
    ) -> tuple[AcquisitionRequest, ...]:
        request_reasons = (reason,) if reasons is None else reasons
        request_deadline = (
            requested_at + max_age
            if deadline_monotonic is None
            else deadline_monotonic
        )
        queued: list[AcquisitionRequest] = []
        for key, grouped_paths in groups:
            existing = self._requests_by_key.get(key)
            if existing is not None:
                request = self._coalesce(
                    existing,
                    paths=grouped_paths,
                    max_age=max_age,
                    priority=priority,
                    reason=reason,
                    reasons=request_reasons,
                    timeout=timeout,
                    requested_at=requested_at,
                    deadline_monotonic=request_deadline,
                )
                self._requests_by_key[key] = request
                queued.append(request)
                continue

            request = self._new_request(
                paths=grouped_paths,
                max_age=max_age,
                priority=priority,
                reason=reason,
                timeout=timeout,
                requested_at=requested_at,
                external_cat_owner=external_cat_owner,
                acquisition_method=key.acquisition_method,
                policy=key.policy,
                reasons=request_reasons,
                deadline_monotonic=request_deadline,
            )
            self._requests_by_key[key] = request
            queued.append(request)
        return tuple(queued)

    def _due_poll_groups(
        self,
        now: float,
    ) -> tuple[tuple[_AcquisitionRequestKey, tuple[FieldPath, ...]], ...]:
        due: list[tuple[_AcquisitionRequestKey, FieldPath]] = []
        for key, paths in self._poll_cadence_groups().items():
            if key in self._requests_by_key or key in self._deferred:
                continue
            policy = key.policy
            if policy.cadence_seconds is None:
                continue
            state = self._cadence_state_for(key, policy, now=now)
            if state.next_due_monotonic <= now:
                due.extend((key, path) for path in paths)

        grouped: dict[_AcquisitionRequestKey, list[FieldPath]] = {}
        for key, path in due:
            grouped.setdefault(key, []).append(path)
        return tuple(
            (key, tuple(sorted(paths, key=str))) for key, paths in grouped.items()
        )

    def _poll_cadence_groups(
        self,
    ) -> dict[_AcquisitionRequestKey, tuple[FieldPath, ...]]:
        grouped: dict[_AcquisitionRequestKey, list[FieldPath]] = {}
        for capability in self._profile.capabilities:
            if not capability.can_poll:
                continue
            policy = self._profile.policy_for(capability.path)
            if policy.cadence_seconds is None:
                continue
            key = _request_key(
                capability.path,
                acquisition_method="poll",
                policy=policy,
            )
            grouped.setdefault(key, []).append(capability.path)
        return {
            key: tuple(sorted(paths, key=str))
            for key, paths in grouped.items()
        }

    def _cadence_state_for(
        self,
        key: _AcquisitionRequestKey,
        policy: AcquisitionPolicy,
        *,
        now: float,
    ) -> _CadenceState:
        existing = self._cadence_by_key.get(key)
        if existing is not None:
            return existing
        assert policy.cadence_seconds is not None
        state = _CadenceState(
            current_cadence_seconds=policy.cadence_seconds,
            next_due_monotonic=now,
        )
        self._cadence_by_key[key] = state
        return state

    def _request_pressure(self) -> dict[str, int]:
        pressure: dict[str, int] = {}
        for request in self._requests_by_key.values():
            _add_pressure(
                pressure,
                priority=request.priority,
                family=request.paths[0].family.value,
            )
        for item in self._deferred.values():
            _add_pressure(
                pressure,
                priority=item.priority,
                family=item.paths[0].family.value,
            )
        return pressure

    def _request_groups(
        self,
        paths: tuple[FieldPath, ...],
    ) -> tuple[tuple[_AcquisitionRequestKey, tuple[FieldPath, ...]], ...]:
        grouped: dict[_AcquisitionRequestKey, list[FieldPath]] = {}
        for path in paths:
            capability = self._profile.capability_for(path)
            policy = self._profile.policy_for(path)
            key = _request_key(
                path,
                acquisition_method=_capability_method(capability, policy),
                policy=policy,
            )
            grouped.setdefault(key, []).append(path)
        return tuple(
            (key, tuple(sorted(group_paths, key=str)))
            for key, group_paths in grouped.items()
        )

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
        acquisition_method: AcquisitionMethod,
        policy: AcquisitionPolicy,
        reasons: tuple[str, ...],
        deadline_monotonic: float,
    ) -> AcquisitionRequest:
        request_id = f"acq-{self._next_id}"
        self._next_id += 1
        return AcquisitionRequest(
            id=request_id,
            paths=paths,
            priority=priority,
            reason=reason,
            reasons=reasons,
            requested_at_monotonic=requested_at,
            deadline_monotonic=deadline_monotonic,
            max_age=max_age,
            timeout=timeout,
            provider=self._profile.provider,
            acquisition_method=acquisition_method,
            policy=policy,
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
        paths: tuple[FieldPath, ...],
        max_age: float,
        priority: AcquisitionPriority,
        reason: str,
        reasons: tuple[str, ...],
        timeout: float | None,
        requested_at: float,
        deadline_monotonic: float,
    ) -> AcquisitionRequest:
        priority_to_keep = (
            priority
            if _PRIORITY_RANK[priority] > _PRIORITY_RANK[existing.priority]
            else existing.priority
        )
        merged_reasons = existing.reasons
        for candidate in reasons:
            if candidate not in merged_reasons:
                merged_reasons = (*merged_reasons, candidate)
        deadline = min(existing.deadline_monotonic, deadline_monotonic)
        merged_paths = _merge_paths(existing.paths, paths)
        return replace(
            existing,
            paths=merged_paths,
            priority=priority_to_keep,
            max_age=min(existing.max_age, max_age),
            timeout=_min_optional_timeout(existing.timeout, timeout),
            deadline_monotonic=deadline,
            reasons=merged_reasons,
            capability_ids=tuple(str(path) for path in merged_paths),
            source_metadata={
                "provider": self._profile.provider,
                "capabilityId": ",".join(str(path) for path in merged_paths),
            },
        )

    def _replace_request_paths(
        self,
        request: AcquisitionRequest,
        *,
        paths: tuple[FieldPath, ...],
    ) -> AcquisitionRequest:
        return replace(
            request,
            paths=paths,
            capability_ids=tuple(str(path) for path in paths),
            source_metadata={
                "provider": self._profile.provider,
                "capabilityId": ",".join(str(path) for path in paths),
            },
        )

    def _defer(
        self,
        key: _AcquisitionRequestKey,
        item: _PendingEnsureFresh,
    ) -> None:
        existing = self._deferred.get(key)
        if existing is None:
            self._deferred[key] = item
            return
        priority = (
            item.priority
            if _PRIORITY_RANK[item.priority] > _PRIORITY_RANK[existing.priority]
            else existing.priority
        )
        max_age = min(existing.max_age, item.max_age)
        timeout = _min_optional_timeout(existing.timeout, item.timeout)
        reason = existing.reason if existing.reason == item.reason else item.reason
        reasons = existing.reasons
        for candidate in item.reasons:
            if candidate not in reasons:
                reasons = (*reasons, candidate)
        self._deferred[key] = _PendingEnsureFresh(
            paths=_merge_paths(existing.paths, item.paths),
            max_age=max_age,
            priority=priority,
            reason=reason,
            reasons=reasons,
            timeout=timeout,
            requested_at_monotonic=min(
                existing.requested_at_monotonic,
                item.requested_at_monotonic,
            ),
            deadline_monotonic=min(
                existing.deadline_monotonic,
                item.deadline_monotonic,
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


class MeterObservationCoalescer:
    """Coalesce short-window meter observations before StateStore apply."""

    __slots__ = ("_coalesced_sample_count", "_dropped_sample_count", "_pending")

    def __init__(self) -> None:
        self._pending: list[_PendingMeterSample] = []
        self._dropped_sample_count = 0
        self._coalesced_sample_count = 0

    def record(
        self,
        observation: Observation,
        policy: MeterCoalescingPolicy,
    ) -> None:
        """Record one meter observation under its coalescing policy."""

        if observation.path.family.value != "meters":
            raise ValueError(f"{observation.path}: meter coalescing requires meters")

        self._pending.append(_PendingMeterSample(observation=observation, policy=policy))
        if policy.max_samples is None:
            return
        overflow = len(self._pending) - policy.max_samples
        if overflow <= 0:
            return
        del self._pending[:overflow]
        self._dropped_sample_count += overflow

    def flush(self, store: StateStore) -> ChangeSet | None:
        """Apply latest pending sample per path and return one coalesced ChangeSet."""

        if not self._pending:
            return None

        samples = self._pending
        self._pending = []
        return self._flush_samples(store, samples=samples)

    def flush_due(self, store: StateStore, *, now: float) -> ChangeSet | None:
        """Flush paths whose latest pending sample has aged past its window."""

        latest_by_path: dict[FieldPath, _PendingMeterSample] = {}
        for sample in self._pending:
            latest_by_path[sample.observation.path] = sample

        due_paths: set[FieldPath] = set()
        for path, sample in latest_by_path.items():
            flush_at = (
                sample.observation.timestamp_monotonic
                + sample.policy.window_seconds
            )
            if flush_at <= now:
                due_paths.add(path)

        if not due_paths:
            return None

        due: list[_PendingMeterSample] = []
        pending: list[_PendingMeterSample] = []
        for sample in self._pending:
            if sample.observation.path in due_paths:
                due.append(sample)
            else:
                pending.append(sample)

        self._pending = pending
        return self._flush_samples(store, samples=due)

    def _flush_samples(
        self,
        store: StateStore,
        *,
        samples: Sequence[_PendingMeterSample],
    ) -> ChangeSet:
        latest_by_path: dict[FieldPath, Observation] = {}
        for sample in samples:
            latest_by_path[sample.observation.path] = sample.observation
        self._coalesced_sample_count += len(samples) - len(latest_by_path)

        changes: list[FieldChange] = []
        sources: list[SourceMetadata] = []
        observed_paths: list[FieldPath] = []
        freshness_paths: list[FieldPath] = []
        result: ChangeSet | None = None
        timestamp_monotonic = max(
            observation.timestamp_monotonic for observation in latest_by_path.values()
        )
        for observation in sorted(
            latest_by_path.values(),
            key=lambda item: str(item.path),
        ):
            result = store.apply(observation)
            changes.extend(result.changes)
            sources.extend(result.sources)
            observed_paths.extend(result.observed_paths)
            freshness_paths.extend(result.freshness_paths)

        assert result is not None
        return ChangeSet(
            revision=result.revision,
            freshness_revision=result.freshness_revision,
            observation_seq=result.observation_seq,
            changes=tuple(changes),
            timestamp_monotonic=timestamp_monotonic,
            sources=tuple(sources),
            coalesced=True,
            observed_paths=tuple(observed_paths),
            freshness_paths=tuple(freshness_paths),
        )

    def next_flush_monotonic(self) -> float | None:
        """Return the earliest monotonic time at which pending samples should flush."""

        if not self._pending:
            return None
        latest_by_path: dict[FieldPath, _PendingMeterSample] = {}
        for sample in self._pending:
            latest_by_path[sample.observation.path] = sample
        return float(
            min(
                sample.observation.timestamp_monotonic + sample.policy.window_seconds
                for sample in latest_by_path.values()
            )
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return JSON-safe coalescing counters."""

        return {
            "pendingSampleCount": len(self._pending),
            "pendingPaths": [
                str(sample.observation.path) for sample in self._pending
            ],
            "droppedSampleCount": self._dropped_sample_count,
            "coalescedSampleCount": self._coalesced_sample_count,
            "nextFlushMonotonic": self.next_flush_monotonic(),
        }


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
            if field.max_age is not None and now - field.last_observed_monotonic > (
                field.max_age
            ):
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
    normalized: tuple[FieldPath, ...]
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


def _request_key(
    path: FieldPath,
    *,
    acquisition_method: AcquisitionMethod,
    policy: AcquisitionPolicy,
) -> _AcquisitionRequestKey:
    return _AcquisitionRequestKey(
        scope=path.scope.value,
        family=path.family.value,
        receiver_id=path.receiver_id,
        slot=None if path.slot is None else path.slot.value,
        acquisition_method=acquisition_method,
        policy=policy,
    )


def _diagnostic_group_key(key: _AcquisitionRequestKey) -> str:
    parts = [
        key.scope,
        key.family,
        "" if key.receiver_id is None else key.receiver_id,
        "" if key.slot is None else key.slot,
        key.acquisition_method,
    ]
    return ":".join(parts)


def _add_pressure(
    pressure: dict[str, int],
    *,
    priority: AcquisitionPriority,
    family: str,
) -> None:
    key = f"{priority.value}:{family}"
    pressure[key] = pressure.get(key, 0) + 1


def _capability_method(
    capability: FieldCapability,
    policy: AcquisitionPolicy,
) -> AcquisitionMethod:
    preferred = ReconciliationPriority(str(policy.reconciliation_priority))
    methods_by_priority: dict[ReconciliationPriority, tuple[AcquisitionMethod, ...]] = {
        ReconciliationPriority.POLL: ("poll",),
        ReconciliationPriority.COMMAND_RESPONSE: ("command_response",),
        ReconciliationPriority.UNSOLICITED: ("wait_for_unsolicited",),
        ReconciliationPriority.LAST_OBSERVATION: (),
    }
    fallback_methods: tuple[AcquisitionMethod, ...] = (
        "poll",
        "command_response",
        "wait_for_unsolicited",
    )
    methods = (*methods_by_priority[preferred], *fallback_methods)
    for method in methods:
        if method == "poll" and capability.can_poll:
            return "poll"
        if (
            method == "command_response"
            and capability.command_response_observable
        ):
            return "command_response"
        if method == "wait_for_unsolicited" and capability.unsolicited_push:
            return "wait_for_unsolicited"
    raise ValueError(f"{capability.path}: no acquisition hook is declared")


def _merge_paths(
    left: tuple[FieldPath, ...],
    right: tuple[FieldPath, ...],
) -> tuple[FieldPath, ...]:
    return tuple(sorted({*left, *right}, key=str))


def _min_optional_timeout(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)
