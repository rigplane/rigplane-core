"""Backend-neutral acquisition scheduling for state freshness repair."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Literal, Protocol

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
    ReconciliationRequest,
    SnapshotDelta,
    StateStore,
)

__all__ = [
    "AcquisitionMethod",
    "AcquisitionExecutionResult",
    "AcquisitionExecutor",
    "AcquisitionPriority",
    "AcquisitionRequest",
    "AcquisitionScheduler",
    "AcquisitionStatus",
    "EnsureFreshResult",
    "IcomCivAcquisitionExecutor",
    "MeterObservationCoalescer",
    "RadioStateModelService",
    "StateFreshnessService",
    "civ_acquisition_executor_for_provider",
    "split_ctl_mem_sub",
]


AcquisitionMethod = Literal["poll", "command_response", "wait_for_unsolicited"]
# ``sub`` is normally a single CI-V sub-command byte (or ``None`` for a
# no-sub-byte read). It is ``bytes`` only for multi-byte ctl-mem
# sub-addressing (0x1A/0x05 "quick set" reads, e.g. voxDelay's 2-byte control
# number, MOR-1483): the first byte is the CI-V sub-command byte and any
# remaining bytes are additional payload data that must follow it in the
# frame. Both ``AcquisitionQuerySender`` implementations (web radio_poller,
# rigctld) must split it via ``split_ctl_mem_sub`` before building the frame.
AcquisitionQuerySender = Callable[
    [int, int | bytes | None, int | None], Awaitable[None]
]
CivCmd29Support = Callable[[int, int | None], bool]


def split_ctl_mem_sub(sub: int | bytes | None) -> tuple[int | None, bytes]:
    """Split an ``AcquisitionQuerySender`` ``sub`` element into CI-V parts.

    Returns ``(civ_sub, extra_data)`` where ``civ_sub`` is the single byte to
    pass as the CI-V frame's sub-command field and ``extra_data`` is any
    additional payload bytes that must follow it (empty for the common
    single-byte-or-none case). Shared by both backend executors so the
    multi-byte ctl-mem representation (see ``AcquisitionQuerySender``) is
    decoded identically everywhere.
    """

    if isinstance(sub, (bytes, bytearray)):
        return (sub[0] if sub else None), bytes(sub[1:])
    return sub, b""


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
class AcquisitionExecutionResult:
    """Backend executor result for one scheduler request attempt."""

    sent_paths: tuple[FieldPath, ...] = ()
    failed_paths: tuple[FieldPath, ...] = ()
    failure_reason: str = ""


class AcquisitionExecutor(Protocol):
    """Backend-owned executor for scheduler acquisition requests."""

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        """Send backend reads for paths not already in flight."""


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
    """Model-service result for a synchronous (fire-and-queue) freshness request.

    Returned by :meth:`StateModelService.ensure_fresh`. The call never awaits
    a backend read, so this result describes only the *state at request time*:

    - ``FRESH`` — the requested fields were already fresh and are returned
      inline in :attr:`fields`; no acquisition was scheduled.
    - ``QUEUED`` — acquisition was enqueued; :attr:`request` carries the
      :class:`AcquisitionRequest` (including its ``timeout``, which bounds the
      *backend executor's* in-flight read, not any caller await). The fields
      are NOT yet fresh; the caller must re-project later or read back.
    - ``DEFERRED`` — acquisition was held off (e.g. external CAT ownership);
      :attr:`message` explains why. Also not yet fresh.
    - ``UNAVAILABLE`` — the path cannot be acquired (unsupported / no hook);
      :attr:`message` carries the diagnostic. The caller must use its own
      fallback or surface unavailability.

    In every non-``FRESH`` case the requested data has not been delivered yet:
    a separate async poller drives the executor afterwards. Callers must not
    treat this result as a completed read.
    """

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


_RECEIVER_IDS: dict[str, int] = {
    "0": 0,
    "main": 0,
    "1": 1,
    "sub": 1,
}
_RECEIVER_LEVEL_QUERY_SUBS: dict[str, int] = {
    "af_level": 0x01,
    "rf_gain": 0x02,
    "squelch": 0x03,
    "apf_type_level": 0x05,
    "nr_level": 0x06,
    "pbt_inner": 0x07,
    "pbt_outer": 0x08,
    "nb_level": 0x12,
    "digisel_shift": 0x13,
}
_RECEIVER_NONLEVEL_QUERIES: dict[str, tuple[int, int | None]] = {
    "att": (0x11, None),
    "preamp": (0x16, 0x02),
    "agc": (0x16, 0x12),
    "audio_peak_filter": (0x16, 0x32),
    # filter_shape (MOR-1491) / manual_notch_width (MOR-1492): documented
    # BCD-nibble 0x16 value reads, same query shape as audio_peak_filter/agc
    # above.
    "filter_shape": (0x16, 0x56),
    "manual_notch_width": (0x16, 0x57),
    "agc_time_constant": (0x1A, 0x04),
    "tone_freq": (0x1B, 0x00),
    "tsql_freq": (0x1B, 0x01),
}
_GLOBAL_TX_TOGGLE_QUERIES: dict[str, tuple[int, int | None]] = {
    "compressor_on": (0x16, 0x44),
    "monitor_on": (0x16, 0x45),
    "vox_on": (0x16, 0x46),
    "split": (0x0F, None),
    "dual_watch": (0x07, 0xC2),
}
_RECEIVER_TOGGLE_QUERIES: dict[str, tuple[int, int | None]] = {
    "digisel": (0x16, 0x4E),
    "ipplus": (0x16, 0x65),
    "nb": (0x16, 0x22),
    "nr": (0x16, 0x40),
    "auto_notch": (0x16, 0x41),
    "manual_notch": (0x16, 0x48),
    "twin_peak_filter": (0x16, 0x4F),
    "repeater_tone": (0x16, 0x42),
    "repeater_tsql": (0x16, 0x43),
}
_GLOBAL_LEVEL_QUERY_SUBS: dict[str, int] = {
    "power_level": 0x0A,
    "mic_gain": 0x0B,
    "cw_pitch": 0x09,
    "key_speed": 0x0C,
    "notch_filter": 0x0D,
    "compressor_level": 0x0E,
    "break_in_delay": 0x0F,
    "drive_gain": 0x14,
    "monitor_gain": 0x15,
    "vox_gain": 0x16,
    "anti_vox_gain": 0x17,
}
# Global operator-control reads that are NOT 0x14 levels. tuner_status is a
# 0x1C 0x01 read with NO data byte; the set form (0x1C 0x01 + 0x00/0x01/0x02)
# is never used here, so a poll only READS ATU status and can never turn the
# tuner on or start a tune (MOR-488 batch 5).
_GLOBAL_NONLEVEL_QUERIES: dict[str, tuple[int, int | None]] = {
    "tuner_status": (0x1C, 0x01),
    # break_in (MOR-1493): documented BCD-nibble 0x16 value read (OFF/SEMI/
    # FULL), same query shape as compressor_on/monitor_on/vox_on above but
    # 3-valued rather than a plain toggle.
    "break_in": (0x16, 0x47),
}
# Global operator-control reads that need the 0x1A ctl-mem ("quick set")
# multi-byte sub-address (MOR-1483): the CI-V sub-command byte (0x05)
# followed by a 2-byte per-model control number, packed together as
# ``bytes`` (see ``AcquisitionQuerySender``'s docstring for the split
# convention). Pinned to IC-7300's control number -- the live reference
# profile (``rigs/ic7300.toml``'s ``get_vox_delay`` = ``1A 05 01 91``). This
# mapping is shared across every icom_civ/xiegu_civ profile and has no
# per-instance radio-model injection, so it cannot vary the control number
# per model; IC-7610 is retired hardware
# (docs/validation/cat-audits/ic7610.md: ``1A 05 0292``) and unverifiable on
# real hardware, so a primed voxDelay read routed through this mapping on an
# IC-7610 profile would address the wrong ctl-mem register.
_GLOBAL_CTL_MEM_QUERIES: dict[str, bytes] = {
    "vox_delay": b"\x05\x01\x91",
}
_GLOBAL_METER_QUERY_SUBS: dict[str, int] = {
    "power": 0x11,
    "swr": 0x12,
    "alc": 0x13,
    "comp": 0x14,
    "vd": 0x15,
    "id": 0x16,
}
_CIV_ACQUISITION_PROVIDERS = frozenset(("icom_civ", "xiegu_civ"))


class IcomCivAcquisitionExecutor:
    """CI-V path-to-query executor for compatible CI-V acquisition profiles."""

    __slots__ = ("_send_query", "_supports_cmd29")

    def __init__(
        self,
        send_query: AcquisitionQuerySender,
        *,
        supports_cmd29: CivCmd29Support | None = None,
    ) -> None:
        self._send_query = send_query
        self._supports_cmd29 = supports_cmd29

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        sent: list[FieldPath] = []
        failed: list[FieldPath] = []
        failure_reason = ""
        for path in request.paths:
            if path in already_sent_paths:
                continue
            query = self.query_for_path(path)
            if query is None:
                failed.append(path)
                failure_reason = failure_reason or "no_civ_query_mapping"
                continue
            command, sub, receiver = query
            if (
                receiver is not None
                and command not in (0x25, 0x26)
                and self._supports_cmd29 is not None
                and not isinstance(sub, (bytes, bytearray))
                and not self._supports_cmd29(command, sub)
            ):
                if receiver != 0:
                    failed.append(path)
                    failure_reason = failure_reason or "no_civ_receiver_route"
                    continue
                receiver = None
            await self._send_query(command, sub, receiver)
            sent.append(path)
        return AcquisitionExecutionResult(
            sent_paths=tuple(sent),
            failed_paths=tuple(failed),
            failure_reason=failure_reason,
        )

    def query_for_path(
        self,
        path: FieldPath,
    ) -> tuple[int, int | bytes | None, int | None] | None:
        receiver = _RECEIVER_IDS.get(path.receiver_id or "")
        if path.scope.value == "receiver" and receiver is None:
            return None
        if path.scope.value == "receiver" and path.family.value == "freq_mode":
            slot = None if path.slot is None else path.slot.value
            if slot in {"A", "B"}:
                return None
            selector = 1 if slot == "unselected" else receiver
            if slot == "unselected" and receiver != 0:
                return None
            if path.name == "freq_hz":
                return (0x25, None, selector)
            if path.name == "mode":
                return (0x26, None, selector)
            if path.name == "filter_width":
                if slot == "unselected":
                    return None
                return (0x1A, 0x03, receiver)
            return None
        if path.scope.value == "receiver" and path.family.value == "meters":
            if path.name == "s_meter":
                return (0x15, 0x02, receiver)
            return None
        if path.scope.value == "receiver" and path.family.value == "operator_toggles":
            toggle = _RECEIVER_TOGGLE_QUERIES.get(path.name)
            return None if toggle is None else (toggle[0], toggle[1], receiver)
        if path.scope.value == "receiver" and path.family.value == "operator_controls":
            nonlevel = _RECEIVER_NONLEVEL_QUERIES.get(path.name)
            if nonlevel is not None:
                return (nonlevel[0], nonlevel[1], receiver)
            sub = _RECEIVER_LEVEL_QUERY_SUBS.get(path.name)
            return None if sub is None else (0x14, sub, receiver)
        if path.scope.value == "global" and path.family.value == "meters":
            sub = _GLOBAL_METER_QUERY_SUBS.get(path.name)
            return None if sub is None else (0x15, sub, None)
        if path.scope.value == "global" and path.family.value == "slow_state":
            if path.name == "active":
                return (0x07, 0xD2, None)
            return None
        if path.scope.value == "global" and path.family.value == "tx_state":
            if path.name == "ptt":
                return (0x1C, 0x00, None)
            if path.name == "rit_on":
                return (0x21, 0x01, None)
            if path.name == "rit_tx":
                return (0x21, 0x02, None)
            toggle = _GLOBAL_TX_TOGGLE_QUERIES.get(path.name)
            return None if toggle is None else (toggle[0], toggle[1], None)
        if path.scope.value == "global" and path.family.value == "operator_controls":
            if path.name == "rit_freq":
                return (0x21, 0x00, None)
            ctl_mem = _GLOBAL_CTL_MEM_QUERIES.get(path.name)
            if ctl_mem is not None:
                return (0x1A, ctl_mem, None)
            nonlevel = _GLOBAL_NONLEVEL_QUERIES.get(path.name)
            if nonlevel is not None:
                return (nonlevel[0], nonlevel[1], None)
            sub = _GLOBAL_LEVEL_QUERY_SUBS.get(path.name)
            return None if sub is None else (0x14, sub, None)
        return None


def civ_acquisition_executor_for_provider(
    provider: str,
    send_query: AcquisitionQuerySender,
    *,
    supports_cmd29: CivCmd29Support | None = None,
) -> AcquisitionExecutor | None:
    """Return the shared CI-V executor for providers using this query envelope."""

    if provider not in _CIV_ACQUISITION_PROVIDERS:
        return None
    return IcomCivAcquisitionExecutor(
        send_query,
        supports_cmd29=supports_cmd29,
    )


_PRIORITY_RANK: dict[AcquisitionPriority, int] = {
    AcquisitionPriority.BACKGROUND: 0,
    AcquisitionPriority.RECONCILIATION: 1,
    AcquisitionPriority.NORMAL: 2,
    AcquisitionPriority.COMMAND: 3,
    AcquisitionPriority.USER: 4,
}
_MIN_RECONCILIATION_MAX_AGE = 1e-9
# MOR-1490 review R2 (Finding 4): cap the number of never-before-queued paths
# a single prime_unobserved() call will enqueue. Uncapped, a profile carrying
# ~20 non-polling field_policies overrides would emit ~20 CI-V frames in one
# drain cycle (~1s of serial time), starving BACKGROUND-priority meter/PTT
# traffic that shares the same lane. StateFreshnessService's bounded,
# adaptive re-derivation (see
# StateFreshnessService.PRIME_ADAPTIVE_INTERVAL_SECONDS /
# .PRIME_REDERIVE_INTERVAL_SECONDS) picks up whatever this call didn't
# reach.
_PRIME_UNOBSERVED_BURST_LIMIT = 5


class AcquisitionScheduler:
    """Minimal priority/dedupe queue for backend-neutral acquisition reads."""

    __slots__ = (
        "_clock",
        "_cadence_by_key",
        "_deferred",
        "_external_cat_owner",
        "_external_cat_paused",
        "_external_cat_reason",
        "_failed_request_count",
        "_failure_count_by_reason",
        "_next_id",
        "_pending_cadence_by_key",
        "_prime_cursor",
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
        self._failed_request_count = 0
        self._failure_count_by_reason: dict[str, int] = {}
        self._next_id = 1
        # Round-robin starting offset into field_policies for
        # prime_unobserved (MOR-1501, A1 from #2415 review): see that
        # method's docstring for why a fixed scan-from-zero starves any
        # reachable field sitting behind >= `limit` permanently-unanswerable
        # leaders in declaration order.
        self._prime_cursor = 0
        self._external_cat_paused = False
        self._external_cat_owner: str | None = None
        self._external_cat_reason = ""

    @property
    def provider(self) -> str:
        """Return the backend/provider id declared by the acquisition profile."""

        provider: str = self._profile.provider
        return provider

    def ensure_fresh(
        self,
        paths: FieldPath | str | Iterable[FieldPath | str],
        *,
        max_age: float,
        priority: AcquisitionPriority | str,
        reason: str,
        timeout: float | None = None,
    ) -> EnsureFreshResult:
        """Queue acquisition for one or more field paths if policy allows it.

        Synchronous and fire-and-queue: this only records the request (or
        defers it under external CAT ownership) and returns immediately. The
        enqueued :class:`AcquisitionRequest` carries ``timeout`` for the
        backend executor's later in-flight read; nothing here awaits it.
        """

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

    def due_requests(
        self, *, now: float | None = None, tx_active: bool = False
    ) -> tuple[AcquisitionRequest, ...]:
        """Queue and return policy-cadence poll requests that are due.

        ``tx_active`` gates cadence groups whose policy carries ``tx_only``
        (MOR-1485): while False, those groups are skipped entirely (not just
        deduped) — no query is sent and their cadence clock is left
        untouched, so the moment a caller passes ``tx_active=True`` a group
        that has been due all along fires immediately rather than waiting a
        fresh cadence interval from the TX-start moment. Callers derive
        ``tx_active`` from their own observed PTT state; this method has no
        opinion on where that comes from.
        """

        timestamp = self._clock.now() if now is None else now
        groups = self._due_poll_groups(timestamp, tx_active=tx_active)
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

    def prime_unobserved(
        self,
        observed_paths: Iterable[FieldPath],
        *,
        reason: str = "prime-unobserved",
        limit: int = _PRIME_UNOBSERVED_BURST_LIMIT,
    ) -> tuple[AcquisitionRequest, ...]:
        """Queue BACKGROUND reads for never-observed fields with a policy.

        ``StateStore.mark_stale_due`` only decays fields already present in
        the store (MOR-432 keeps decay externally driven, but it still can't
        decay what was never entered), and :meth:`due_requests` only cadence-
        polls capabilities flagged ``polling=True``. A field carrying an
        explicit :attr:`RadioAcquisitionProfile.field_policies` override that
        is neither polled nor ever observed would otherwise stay ``UNKNOWN``
        forever — no reconciliation hint is ever generated for it (MOR-1490).

        The caller (:class:`StateFreshnessService`) re-derives the never-
        observed set at a bounded, adaptive interval (see
        :data:`StateFreshnessService.PRIME_ADAPTIVE_INTERVAL_SECONDS` and
        :data:`StateFreshnessService.PRIME_REDERIVE_INTERVAL_SECONDS`)
        rather than once per connect epoch — a dropped/unanswered prime must
        not leave a field ``UNKNOWN`` for the life of the connection
        (MOR-1490 review R2, Finding 1). Every call runs at
        :data:`AcquisitionPriority.BACKGROUND` so priming never competes with
        fast meters/PTT. Unsupported/hookless paths fall out through the
        normal :meth:`ensure_fresh` availability gate, and an unanswered
        prime is dropped by the existing :meth:`record_acquisition_failure`
        accounting — no bespoke retry loop. In production the very first
        prime typically runs while the store is still near-empty (the
        backend's initial fetch is still in flight), so most policy fields
        prime on every start — that is expected and self-extinguishing: the
        moment a field lands its first observation it drops out of both the
        ``observed_paths`` gate here and the ``field_policies`` sweep never
        matters again for it.

        **Cadence-owned paths are skipped entirely**, not just deduped
        against an in-flight request (MOR-1490 review R3): a
        ``field_policies`` override doesn't imply the field is
        *unpolled* — on the shipped IC-7300 profile all six overrides
        (``s_meter``, ``ptt``, and the four 15s-cadence gain fields) sit on
        capabilities with ``polling=True``. Priming one of those anyway
        queues a request under the exact same
        ``_AcquisitionRequestKey`` that :meth:`due_requests`'s
        ``_due_poll_groups`` groups by, and that method skips a whole
        cadence *group* — not just the one path — the instant its key is
        already present in the pending-request table. Concretely: priming
        one of the four gain fields sharing one cadence key silently starved
        the group's ordinary t0 poll for the other three (measured live on
        IC-7300: ``anti_vox_gain``'s first observation moved from t=0 to
        t=+15s). A path whose capability is pollable and whose resolved
        policy carries a ``cadence_seconds`` is therefore left to
        :meth:`due_requests` entirely; this method never touches it,
        regardless of whether it happens to also carry a ``field_policies``
        entry. On the shipped IC-7300 profile ``prime_unobserved`` now
        actively primes the non-polling ``command_response`` field-policy
        membership added by MOR-1483/1491/1492/1493 (VOX/MON toggles,
        filter/PBT facts, DSP level facts, RIT/XIT and CW keyer facts) —
        this mechanism was previously wired but exercised by nothing on any
        shipped profile.

        Two further guards keep one call from flooding the transport:

        - **Already-pending skip**: a (non-cadence-owned) path with an
          outstanding, unanswered request from a previous prime call is
          skipped — re-submitting it would just coalesce into the same
          request without sending an additional frame, so it doesn't need to
          consume this call's budget. Because it doesn't consume budget,
          the very next call to this method reaches paths a prior call left
          short of the burst cap below — reaching them only requires this
          method to be invoked again, not for the earlier paths to have
          been *answered*. In production, "invoked again" is gated by
          :data:`StateFreshnessService.PRIME_ADAPTIVE_INTERVAL_SECONDS`
          (~5s, while any policy field remains unobserved) or, once the
          never-observed set has emptied,
          :data:`StateFreshnessService.PRIME_REDERIVE_INTERVAL_SECONDS`
          (~30s) — so that interval, not the leading paths' resolution, is
          the practical bound on how long a capped-out straggler waits.
        - **Burst cap** (``limit``, default
          :data:`_PRIME_UNOBSERVED_BURST_LIMIT`): at most ``limit`` *new*
          paths are queued per call, so a profile with many non-polling
          policy fields can't emit a burst of CI-V frames in one drain cycle
          (MOR-1490 review R2, Finding 4). Fields are visited starting from
          ``_prime_cursor`` — a round-robin offset into ``field_policies``
          (profile-declaration order) that this method advances past
          whatever it scanned, wrapping at the end of the mapping (MOR-1501,
          A1 from #2415 review) — rather than always restarting the scan at
          index 0. A fixed scan-from-zero would let five or more
          *permanently* unanswerable non-polling fields ahead of a reachable
          one in declaration order starve that reachable one indefinitely:
          each call would re-queue the same unanswerable leaders (freed back
          to "not observed, not pending" the moment their request fails —
          see :meth:`record_acquisition_failure`) and never reach past them.
          Rotating the start point instead guarantees every field gets
          scanned at least once every ``ceil(len(field_policies) / limit)``
          calls regardless of how many leaders ahead of it are stuck.

        The returned tuple has at most one entry per distinct
        :class:`AcquisitionRequest` id — multiple primed paths that coalesce
        into the same request (identical scope/family/receiver/slot/
        acquisition-method/policy) are represented once, carrying the
        merged ``paths`` (MOR-1490 review R2, Finding 3).
        """

        observed = frozenset(observed_paths)
        pending = frozenset(
            path for request in self._requests_by_key.values() for path in request.paths
        )
        items = tuple(self._profile.field_policies.items())
        total = len(items)
        queued_by_id: dict[str, AcquisitionRequest] = {}
        queued_path_count = 0
        cursor = self._prime_cursor % total if total else 0
        visited = 0
        for offset in range(total):
            if queued_path_count >= limit:
                break
            visited = offset + 1
            path, policy = items[(cursor + offset) % total]
            if path in observed or path in pending:
                continue
            if (
                self._profile.capability_for(path).can_poll
                and policy.cadence_seconds is not None
            ):
                # due_requests() already owns this path via its cadence
                # group; priming it here would collide with that group's
                # request key and starve due_requests()'s t0 poll for every
                # other path sharing the group (MOR-1490 review R3).
                continue
            max_age = policy.freshness_ttl_seconds
            if max_age is None or max_age <= 0:
                max_age = policy.cadence_seconds
            if max_age is None or max_age <= 0:
                max_age = _MIN_RECONCILIATION_MAX_AGE
            result = self.ensure_fresh(
                (path,),
                max_age=max_age,
                priority=AcquisitionPriority.BACKGROUND,
                reason=reason,
            )
            if result.request is None:
                continue
            queued_path_count += 1
            queued_by_id[result.request.id] = result.request
        if total:
            self._prime_cursor = (cursor + visited) % total
        return tuple(queued_by_id.values())

    def has_unobserved_policy_fields(
        self,
        observed_paths: Iterable[FieldPath],
    ) -> bool:
        """Return True if any explicit ``field_policies`` path is unobserved.

        Shares :meth:`prime_unobserved`'s eligibility rules for *which*
        fields it is responsible for (excludes cadence/poll-owned paths,
        which are :meth:`due_requests`'s concern) but, unlike
        ``prime_unobserved``, does NOT exclude already-pending paths: a path
        with an outstanding, unanswered prime read is still unobserved, and
        :class:`StateFreshnessService` uses this to decide whether to keep
        re-deriving the prime sweep on its fast cadence or back off once the
        never-observed set has genuinely emptied (MOR-1501).
        """

        observed = frozenset(observed_paths)
        for path, policy in self._profile.field_policies.items():
            if path in observed:
                continue
            if (
                self._profile.capability_for(path).can_poll
                and policy.cadence_seconds is not None
            ):
                continue
            return True
        return False

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
                semantic_changed = semantic_changed or pending_cadence.semantic_changed
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

    def record_acquisition_failure(
        self,
        request: AcquisitionRequest,
        *,
        reason: str,
        failed_paths: Iterable[FieldPath] | None = None,
        now: float | None = None,
        link_healthy: bool = True,
    ) -> None:
        """Complete failed paths and advance cadence to avoid immediate resend.

        ``link_healthy`` gates the false-timeout path (MOR-874): an
        ``acquisition_request_timeout`` reported while the underlying transport
        is healthy (sub-second round-trips, no recovery in progress) is not a
        real backend failure — the radio answered, the deadline simply fired
        first under load. Such an event must NOT count as a failure, must NOT
        drop the request from the pending queue, and must NOT advance cadence
        (which is what later decays ``freq_mode``/``tx_state``/``slow_state`` to
        the 30 s backoff ceiling). The caller leaves the request in flight so
        the returning observation can still credit it.
        """

        failure_reason = reason or "acquisition_failed"
        if failure_reason == "acquisition_request_timeout" and link_healthy:
            return

        requested_paths = frozenset(request.paths)
        failed = requested_paths if failed_paths is None else frozenset(failed_paths)
        failed = failed.intersection(requested_paths)
        if not failed:
            return

        self._failed_request_count += 1
        self._failure_count_by_reason[failure_reason] = (
            self._failure_count_by_reason.get(failure_reason, 0) + 1
        )

        key = _request_key(
            request.paths[0],
            acquisition_method=request.acquisition_method,
            policy=request.policy,
        )
        existing = self._requests_by_key.get(key)
        if existing is not None and existing.id == request.id:
            remaining_paths = tuple(
                path for path in existing.paths if path not in failed
            )
            if remaining_paths:
                self._requests_by_key[key] = self._replace_request_paths(
                    existing,
                    paths=remaining_paths,
                )
            else:
                del self._requests_by_key[key]
                self._pending_cadence_by_key.pop(key, None)

        if request.policy.cadence_seconds is None:
            return
        timestamp = self._clock.now() if now is None else now
        previous = self._cadence_state_for(key, request.policy, now=timestamp)
        self._cadence_by_key[key] = _CadenceState(
            current_cadence_seconds=previous.current_cadence_seconds,
            next_due_monotonic=timestamp + previous.current_cadence_seconds,
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
            "failedRequestCount": self._failed_request_count,
            "failureCountByReason": dict(self._failure_count_by_reason),
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
            requested_at + max_age if deadline_monotonic is None else deadline_monotonic
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
            requested_at + max_age if deadline_monotonic is None else deadline_monotonic
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
        *,
        tx_active: bool = False,
    ) -> tuple[tuple[_AcquisitionRequestKey, tuple[FieldPath, ...]], ...]:
        due: list[tuple[_AcquisitionRequestKey, FieldPath]] = []
        for key, paths in self._poll_cadence_groups().items():
            if key in self._requests_by_key or key in self._deferred:
                continue
            policy = key.policy
            if policy.cadence_seconds is None:
                continue
            if policy.tx_only and not tx_active:
                # MOR-1485: skip entirely rather than dedupe/defer — no query
                # sent, no cadence clock touched, so a group due since before
                # TX started fires on the very next tx_active=True call.
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
        return {key: tuple(sorted(paths, key=str)) for key, paths in grouped.items()}

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

        self._pending.append(
            _PendingMeterSample(observation=observation, policy=policy)
        )
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
                sample.observation.timestamp_monotonic + sample.policy.window_seconds
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
            "pendingPaths": [str(sample.observation.path) for sample in self._pending],
            "droppedSampleCount": self._dropped_sample_count,
            "coalescedSampleCount": self._coalesced_sample_count,
            "nextFlushMonotonic": self.next_flush_monotonic(),
        }


class StateFreshnessService:
    """Advance StateStore freshness and enqueue reconciliation requests.

    This service supplies the freshness decay that :class:`StateStore` does
    NOT perform on its own (MOR-432). Decay only happens while this service is
    driven: either by awaiting :meth:`run` (the production path — the web and
    rigctld servers create a background task for it over the canonical store)
    or by calling :meth:`tick` directly (tests). A store with no wired+running
    ``StateFreshnessService`` never ages its fields to ``STALE``; bare
    ``StateStore()`` fallback sites are non-decaying by design and are not the
    production delivery store.
    """

    #: Fast re-derivation spacing used WHILE at least one explicit
    #: ``field_policies`` field remains unobserved (MOR-1501, verifier-
    #: prescribed on #2421's review). The flat 30s interval below left a
    #: real IC-7300 connect with a ~120s populate tail for its 23 non-polling
    #: policy fields: ``ceil(23 / _PRIME_UNOBSERVED_BURST_LIMIT) == 5`` waves
    #: at 30s apart, even though each field's true CI-V round-trip cost is
    #: ~1.1s. At 5s spacing the same 5 waves complete in ~20-25s — the burst
    #: cap (unchanged, still the lane-protection knob) is what still bounds
    #: each wave's size, this constant only bounds how long a capped-out
    #: straggler waits between waves. See
    #: :meth:`_reprime_unobserved_if_due` for the dead-link write-rate
    #: consequence of shortening this interval.
    PRIME_ADAPTIVE_INTERVAL_SECONDS: float = 5.0

    #: Re-derivation spacing once the never-observed ``field_policies`` set
    #: has genuinely emptied (MOR-1490 review R2, Finding 1; backoff target
    #: added MOR-1501). A one-shot "prime once per connect epoch" flag means
    #: a single dropped/unanswered prime read leaves the field ``UNKNOWN``
    #: until the process restarts — reconnects don't rebuild this service,
    #: so nothing ever re-arms the flag. Re-deriving on a bounded interval
    #: instead is self-extinguishing: once every policy field is observed,
    #: :meth:`AcquisitionScheduler.has_unobserved_policy_fields` goes False
    #: and :meth:`_reprime_unobserved_if_due` backs off to this slower
    #: interval so a fully-populated store isn't paying the fast (5s) check
    #: forever.
    PRIME_REDERIVE_INTERVAL_SECONDS: float = 30.0

    __slots__ = (
        "_interval_seconds",
        "_next_prime_monotonic",
        "_on_delta",
        "_scheduler",
        "_store",
    )

    def __init__(
        self,
        *,
        store: StateStore,
        scheduler: AcquisitionScheduler | None = None,
        interval_seconds: float = 0.05,
        on_delta: Callable[[SnapshotDelta], None] | None = None,
    ) -> None:
        _validate_positive(interval_seconds, label="interval_seconds")
        self._store = store
        self._scheduler = scheduler
        self._interval_seconds = interval_seconds
        self._on_delta = on_delta
        # -inf so the first tick always primes immediately, regardless of
        # what monotonic clock value the caller starts at.
        self._next_prime_monotonic = float("-inf")

    def tick(self, *, now: float | None = None) -> SnapshotDelta:
        """Advance stale fields once and queue reconciliation through scheduler.

        Invariant: ``now`` (explicit or defaulted) must come from the same
        monotonic domain as ``self._next_prime_monotonic`` — callers that
        pass a manually-seeded :class:`FreshnessClock` value must do so on
        every call, since the default fallback is real ``time.monotonic()``
        and mixing the two domains within one service instance would make
        the bounded re-derivation interval comparison meaningless.
        """

        timestamp = time.monotonic() if now is None else now
        self._reprime_unobserved_if_due(now=timestamp)
        delta = self._store.mark_stale_due(now=now)
        for request in delta.reconciliation_requests:
            self._queue_reconciliation(request)
        if (delta.freshness or delta.reconciliation_requests) and self._on_delta:
            self._on_delta(delta)
        return delta

    def _reprime_unobserved_if_due(self, *, now: float) -> None:
        """Re-derive the never-observed-field prime at an adaptive interval.

        Replaces the earlier "once per connect epoch" latch (MOR-1490 review
        R2, Finding 1): that guard never reset on reconnect and permanently
        stranded a field at ``UNKNOWN`` if its one prime read was dropped.
        The re-derivation spacing is itself adaptive (MOR-1501): it runs
        every :data:`PRIME_ADAPTIVE_INTERVAL_SECONDS` while
        :meth:`AcquisitionScheduler.has_unobserved_policy_fields` reports at
        least one explicit ``field_policies`` path still unobserved, and
        backs off to the slower :data:`PRIME_REDERIVE_INTERVAL_SECONDS` the
        moment that set empties — cheap either way (one ``store.snapshot()``
        plus a loop over ``field_policies``), so paying it more often while
        fields are still missing is a fine trade against the ~120s populate
        tail the flat 30s interval produced.

        Dead-link write-rate honesty (R3 lesson from MOR-1490's own review,
        applies again here): a capped-out straggler left short of the burst
        cap costs nothing extra — it was skipped without being queued, so no
        frame was sent for it. But a *leader* that WAS queued and then fails
        (:meth:`AcquisitionScheduler.record_acquisition_failure`, e.g. an
        unanswered field on a genuinely dead link) is freed back to "not
        observed, not pending" and becomes eligible to be re-queued on the
        very next re-derivation. Re-queueing is not free: every request
        :meth:`AcquisitionScheduler.pending_requests` yields is later drained
        by a real backend executor (e.g. ``IcomCivAcquisitionExecutor``)
        that performs an actual ``send_query`` write to the wire — nothing
        here is a no-op retry. Shortening the interval from 30s to 5s
        therefore genuinely raises the worst-case re-attempt rate on a fully
        dead link by ~6x: at most ``limit`` (the burst cap, unchanged)
        frames per adaptive-interval window, i.e. <= 5 frames / 5s = 1
        frame/s worst case, versus <= 5 frames / 30s ~= 0.167 frames/s
        before. The count per window still cannot exceed the burst cap
        (bounded, not growing) — the wire load stays a small, constant
        fraction of the ~20 q/s serial ceiling
        (``_SERIAL_DEFAULT_CIV_MIN_INTERVAL_MS``); it does not accumulate
        across windows because record_acquisition_failure clears the
        request rather than leaving it queued twice.
        """

        scheduler = self._scheduler
        if scheduler is None:
            return
        if now < self._next_prime_monotonic:
            return
        observed = tuple(field.path for field in self._store.snapshot().fields)
        scheduler.prime_unobserved(observed)
        still_unobserved = scheduler.has_unobserved_policy_fields(observed)
        interval = (
            self.PRIME_ADAPTIVE_INTERVAL_SECONDS
            if still_unobserved
            else self.PRIME_REDERIVE_INTERVAL_SECONDS
        )
        self._next_prime_monotonic = now + interval

    async def run(self) -> None:
        """Run the periodic freshness loop until cancelled by the host."""

        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                self.tick()
        except asyncio.CancelledError:
            pass

    def _queue_reconciliation(self, request: ReconciliationRequest) -> None:
        scheduler = self._scheduler
        if scheduler is None:
            return
        max_age = request.max_age
        if max_age is None or max_age <= 0:
            max_age = _MIN_RECONCILIATION_MAX_AGE
        scheduler.ensure_fresh(
            (request.path,),
            max_age=max_age,
            priority=AcquisitionPriority.RECONCILIATION,
            reason=request.reason,
        )


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
        """Return fresh snapshots or queue acquisition through the scheduler.

        Synchronous and non-blocking (see :class:`StateModelService`). Fresh
        fields are returned inline; otherwise the request is delegated to
        :meth:`AcquisitionScheduler.ensure_fresh`, which only *enqueues* the
        backend request and returns at once. ``timeout`` is forwarded onto the
        enqueued ``AcquisitionRequest`` for the backend executor; this method
        never awaits the read.
        """

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
        if method == "command_response" and capability.command_response_observable:
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
