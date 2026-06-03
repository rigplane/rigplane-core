"""Minimal acquisition scheduler behavior for MOR-339."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
    AcquisitionStatus,
    MeterObservationCoalescer,
    RadioStateModelService,
)
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    AdaptiveDecayPolicy,
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
from rigplane.core.state_store import FreshnessClock, FreshnessState, StateStore


def _source() -> SourceMetadata:
    return SourceMetadata(source="poll_response", provider="test", transport="fake")


def _observation(
    path: FieldPath,
    value: Any,
    *,
    at: float,
    max_age: float | None = None,
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_source(),
        timestamp_monotonic=at,
        max_age=max_age,
    )


def _changeset(
    *,
    changes: tuple[FieldChange, ...] = (),
    at: float,
) -> ChangeSet:
    return ChangeSet(
        revision=1 if changes else 0,
        freshness_revision=1,
        observation_seq=1,
        changes=changes,
        timestamp_monotonic=at,
        sources=(_source(),),
    )


def _profile(
    paths: Iterable[FieldPath],
    *,
    default_policy: AcquisitionPolicy | None = None,
    field_policies: dict[FieldPath, AcquisitionPolicy] | None = None,
) -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=tuple(
            FieldCapability(
                path=path,
                polling=True,
                stream_like=path.family.value == "meters",
                command_response_observable=path.family.value == "operator_controls",
                supported_controls=(
                    ("set_level",) if path.family.value == "operator_controls" else ()
                ),
            )
            for path in paths
        ),
        default_policy=default_policy or AcquisitionPolicy(),
        field_policies=field_policies or {},
    )


def test_model_service_returns_fresh_snapshot_without_queueing_acquisition() -> None:
    clock = FreshnessClock(start=10.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    store = StateStore(freshness_clock=clock)
    store.apply(_observation(freq, 14_074_000, at=clock.now(), max_age=5.0))
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)
    service = RadioStateModelService(store=store, scheduler=scheduler, clock=clock)

    result = service.ensure_fresh(
        freq,
        max_age=2.0,
        priority=AcquisitionPriority.USER,
        reason="rigctld-get",
    )

    assert result.status is AcquisitionStatus.FRESH
    assert result.fields[0].path == freq
    assert result.fields[0].value == 14_074_000
    assert result.request is None
    assert scheduler.pending_requests() == ()


def test_model_service_queues_backend_neutral_request_for_stale_field() -> None:
    clock = FreshnessClock(start=20.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    store = StateStore(freshness_clock=clock)
    store.apply(_observation(freq, 7_074_000, at=clock.now(), max_age=0.5))
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)
    service = RadioStateModelService(store=store, scheduler=scheduler, clock=clock)
    clock.advance(0.6)
    store.mark_stale_due()

    result = service.ensure_fresh(
        freq,
        max_age=0.25,
        priority="user",
        reason="web-snapshot",
        timeout=1.5,
    )

    assert result.status is AcquisitionStatus.QUEUED
    assert result.request is not None
    assert result.request.paths == (freq,)
    assert result.request.provider == "test_provider"
    assert result.request.priority is AcquisitionPriority.USER
    assert result.request.reason == "web-snapshot"
    assert result.request.max_age == 0.25
    assert result.request.timeout == 1.5
    assert result.request.deadline_monotonic == 20.85
    assert result.request.acquisition_method == "poll"
    assert result.request.policy.freshness_ttl_seconds == 15.0
    assert scheduler.pending_requests() == (result.request,)


def test_duplicate_requests_coalesce_with_highest_priority_and_urgent_deadline() -> (
    None
):
    clock = FreshnessClock(start=30.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)

    low = scheduler.ensure_fresh(
        freq,
        max_age=5.0,
        priority=AcquisitionPriority.BACKGROUND,
        reason="telemetry",
        timeout=10.0,
    )
    clock.advance(1.0)
    high = scheduler.ensure_fresh(
        freq,
        max_age=0.5,
        priority=AcquisitionPriority.USER,
        reason="user-read",
        timeout=2.0,
    )

    assert low.status is AcquisitionStatus.QUEUED
    assert high.status is AcquisitionStatus.QUEUED
    assert high.request is not None
    assert low.request is not None
    assert high.request.id == low.request.id
    assert high.request.priority is AcquisitionPriority.USER
    assert high.request.max_age == 0.5
    assert high.request.deadline_monotonic == 31.5
    assert high.request.reasons == ("telemetry", "user-read")
    assert scheduler.pending_requests() == (high.request,)


def test_same_family_requests_share_one_acquisition_request() -> None:
    clock = FreshnessClock(start=35.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    mode = FieldPath.active("main", "freq_mode", "mode")
    scheduler = AcquisitionScheduler(profile=_profile([freq, mode]), clock=clock)

    freq_result = scheduler.ensure_fresh(
        freq,
        max_age=5.0,
        priority="background",
        reason="background-freq",
        timeout=10.0,
    )
    clock.advance(1.0)
    mode_result = scheduler.ensure_fresh(
        mode,
        max_age=0.5,
        priority="user",
        reason="visible-mode",
        timeout=2.0,
    )

    assert freq_result.request is not None
    assert mode_result.request is not None
    assert mode_result.request.id == freq_result.request.id
    assert mode_result.request.paths == (freq, mode)
    assert mode_result.request.priority is AcquisitionPriority.USER
    assert mode_result.request.deadline_monotonic == 36.5
    assert mode_result.request.max_age == 0.5
    assert mode_result.request.timeout == 2.0
    assert mode_result.request.reasons == ("background-freq", "visible-mode")
    assert scheduler.pending_requests() == (mode_result.request,)


def test_recording_older_coalesced_request_preserves_newer_pending_paths() -> None:
    clock = FreshnessClock(start=36.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    mode = FieldPath.active("main", "freq_mode", "mode")
    scheduler = AcquisitionScheduler(profile=_profile([freq, mode]), clock=clock)

    freq_result = scheduler.ensure_fresh(
        freq,
        max_age=5.0,
        priority="background",
        reason="background-freq",
        timeout=10.0,
    )
    assert freq_result.request is not None
    stale_executor_copy = freq_result.request
    mode_result = scheduler.ensure_fresh(
        mode,
        max_age=0.5,
        priority="user",
        reason="visible-mode",
        timeout=2.0,
    )
    assert mode_result.request is not None
    assert mode_result.request.id == stale_executor_copy.id
    assert mode_result.request.paths == (freq, mode)

    scheduler.record_acquisition_result(
        stale_executor_copy,
        _changeset(
            changes=(FieldChange(path=freq, previous=7_074_000, current=14_074_000),),
            at=clock.now(),
        ),
    )

    pending = scheduler.pending_requests()
    assert len(pending) == 1
    assert pending[0].id == stale_executor_copy.id
    assert pending[0].paths == (mode,)


def test_user_facing_requests_preempt_background_telemetry() -> None:
    clock = FreshnessClock(start=40.0)
    meter = FieldPath.receiver("main", "meters", "s_meter")
    mode = FieldPath.active("main", "freq_mode", "mode")
    scheduler = AcquisitionScheduler(profile=_profile([meter, mode]), clock=clock)

    background = scheduler.ensure_fresh(
        meter,
        max_age=1.0,
        priority="background",
        reason="meter-tick",
    )
    user = scheduler.ensure_fresh(
        mode,
        max_age=1.0,
        priority="user",
        reason="visible-mode",
    )

    assert background.request is not None
    assert user.request is not None
    assert scheduler.pending_requests() == (user.request, background.request)


def test_external_cat_pause_defers_conflicting_polling_and_resume_queues_it() -> None:
    clock = FreshnessClock(start=50.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)

    scheduler.pause_external_cat(owner="hamlib-client", reason="raw-cat-session")
    paused = scheduler.ensure_fresh(
        freq,
        max_age=0.5,
        priority="user",
        reason="rigctld-get",
    )

    assert paused.status is AcquisitionStatus.DEFERRED
    assert paused.request is None
    assert scheduler.pending_requests() == ()
    resumed = scheduler.resume_external_cat()
    assert len(resumed) == 1
    assert resumed[0].paths == (freq,)
    assert resumed[0].external_cat_owner == "hamlib-client"
    assert scheduler.pending_requests() == resumed


def test_external_cat_pause_hides_existing_conflicting_poll_request() -> None:
    clock = FreshnessClock(start=51.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)

    result = scheduler.ensure_fresh(
        freq,
        max_age=0.5,
        priority="background",
        reason="background-poll",
    )
    assert result.request is not None
    assert scheduler.pending_requests() == (result.request,)

    scheduler.pause_external_cat(owner="hamlib-client", reason="raw-cat-session")

    assert scheduler.pending_requests() == ()


def test_external_cat_continue_policy_allows_non_conflicting_request() -> None:
    clock = FreshnessClock(start=60.0)
    ptt = FieldPath.global_("tx_state", "ptt")
    profile = _profile(
        [ptt],
        default_policy=AcquisitionPolicy(
            external_cat_pause=ExternalCatPauseBehavior.CONTINUE,
        ),
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    scheduler.pause_external_cat(owner="hamlib-client")
    result = scheduler.ensure_fresh(
        ptt,
        max_age=1.0,
        priority="reconciliation",
        reason="ptt-reconcile",
    )

    assert result.status is AcquisitionStatus.QUEUED
    assert result.request is not None
    assert result.request.external_cat_paused is True


def test_external_cat_pause_preserves_existing_continue_policy_request() -> None:
    clock = FreshnessClock(start=61.0)
    ptt = FieldPath.global_("tx_state", "ptt")
    profile = _profile(
        [ptt],
        default_policy=AcquisitionPolicy(
            external_cat_pause=ExternalCatPauseBehavior.CONTINUE,
        ),
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    result = scheduler.ensure_fresh(
        ptt,
        max_age=1.0,
        priority="reconciliation",
        reason="ptt-reconcile",
    )
    assert result.request is not None

    scheduler.pause_external_cat(owner="hamlib-client", reason="raw-cat-session")

    assert scheduler.pending_requests() == (result.request,)


def test_external_cat_pause_defers_only_pause_policy_groups() -> None:
    clock = FreshnessClock(start=62.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    ptt = FieldPath.global_("tx_state", "ptt")
    profile = _profile(
        [freq, ptt],
        field_policies={
            freq: AcquisitionPolicy(
                external_cat_pause=ExternalCatPauseBehavior.PAUSE_POLLING,
            ),
            ptt: AcquisitionPolicy(
                external_cat_pause=ExternalCatPauseBehavior.CONTINUE,
            ),
        },
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    scheduler.pause_external_cat(owner="hamlib-client")
    result = scheduler.ensure_fresh(
        [ptt, freq],
        max_age=1.0,
        priority="user",
        reason="mixed-snapshot",
    )

    assert result.status is AcquisitionStatus.QUEUED
    assert result.request is not None
    assert result.request.paths == (ptt,)
    assert result.request.external_cat_paused is True
    assert scheduler.pending_requests() == (result.request,)

    resumed = scheduler.resume_external_cat()

    assert len(resumed) == 1
    assert resumed[0].paths == (freq,)
    assert scheduler.pending_requests() == (result.request, resumed[0])


def test_external_cat_resume_requeues_existing_deferred_request_once() -> None:
    clock = FreshnessClock(start=62.5)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)

    result = scheduler.ensure_fresh(
        freq,
        max_age=0.5,
        priority="background",
        reason="background-poll",
        timeout=2.0,
    )
    assert result.request is not None

    scheduler.pause_external_cat(owner="hamlib-client", reason="raw-cat-session")
    assert scheduler.pending_requests() == ()

    resumed = scheduler.resume_external_cat()

    assert len(resumed) == 1
    assert resumed[0].paths == (freq,)
    assert resumed[0].priority is AcquisitionPriority.BACKGROUND
    assert resumed[0].reason == "background-poll"
    assert resumed[0].reasons == ("background-poll",)
    assert resumed[0].max_age == 0.5
    assert resumed[0].timeout == 2.0
    assert resumed[0].requested_at_monotonic == 62.5
    assert resumed[0].deadline_monotonic == 63.0
    assert resumed[0].external_cat_owner == "hamlib-client"
    assert scheduler.pending_requests() == resumed
    assert scheduler.resume_external_cat() == ()
    assert scheduler.pending_requests() == resumed


def test_external_cat_resume_dedupes_same_family_deferred_requests() -> None:
    clock = FreshnessClock(start=63.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    mode = FieldPath.active("main", "freq_mode", "mode")
    scheduler = AcquisitionScheduler(profile=_profile([freq, mode]), clock=clock)

    scheduler.pause_external_cat(owner="hamlib-client")
    freq_result = scheduler.ensure_fresh(
        freq,
        max_age=5.0,
        priority="background",
        reason="background-freq",
    )
    clock.advance(1.0)
    mode_result = scheduler.ensure_fresh(
        mode,
        max_age=0.5,
        priority="user",
        reason="visible-mode",
    )

    assert freq_result.status is AcquisitionStatus.DEFERRED
    assert mode_result.status is AcquisitionStatus.DEFERRED
    assert scheduler.pending_requests() == ()

    resumed = scheduler.resume_external_cat()

    assert len(resumed) == 1
    assert resumed[0].paths == (freq, mode)
    assert resumed[0].priority is AcquisitionPriority.USER
    assert resumed[0].max_age == 0.5
    assert resumed[0].deadline_monotonic == 64.5
    assert resumed[0].reasons == ("background-freq", "visible-mode")
    assert scheduler.pending_requests() == resumed


def test_capability_metadata_reports_unavailable_without_backend_delivery() -> None:
    clock = FreshnessClock(start=70.0)
    power = FieldPath.global_("tx_state", "power_on")
    profile = RadioAcquisitionProfile(
        provider="external_rigctld",
        capabilities=(
            FieldCapability(
                path=power,
                availability=FieldAvailability.UNSUPPORTED,
                diagnostic="Hamlib model does not expose power state",
            ),
        ),
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    result = scheduler.ensure_fresh(
        power,
        max_age=1.0,
        priority="user",
        reason="startup-read",
    )

    assert result.status is AcquisitionStatus.UNAVAILABLE
    assert result.request is None
    assert "does not expose power state" in result.message


def test_policy_inputs_for_meters_and_slow_controls_are_preserved_on_request() -> None:
    clock = FreshnessClock(start=80.0)
    meter = FieldPath.receiver("main", "meters", "s_meter")
    af_level = FieldPath.receiver("main", "operator_controls", "af_level")
    profile = _profile(
        [meter, af_level],
        field_policies={
            meter: AcquisitionPolicy(
                cadence_seconds=0.2,
                freshness_ttl_seconds=0.6,
                meter_coalescing=MeterCoalescingPolicy(window_seconds=0.1),
            ),
            af_level: AcquisitionPolicy(
                cadence_seconds=30.0,
                freshness_ttl_seconds=120.0,
            ),
        },
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    meter_result = scheduler.ensure_fresh(
        meter,
        max_age=0.3,
        priority="background",
        reason="meter-refresh",
    )
    control_result = scheduler.ensure_fresh(
        af_level,
        max_age=60.0,
        priority="normal",
        reason="settings-panel",
    )

    assert meter_result.request is not None
    assert control_result.request is not None
    assert meter_result.request.policy.meter_coalescing is not None
    assert meter_result.request.policy.meter_coalescing.window_seconds == 0.1
    assert control_result.request.policy.cadence_seconds == 30.0
    assert control_result.request.acquisition_method == "poll"


def test_mixed_pollable_and_unsolicited_paths_are_not_emitted_as_one_poll() -> None:
    clock = FreshnessClock(start=85.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    ptt = FieldPath.global_("tx_state", "ptt")
    profile = RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(
            FieldCapability(path=freq, polling=True),
            FieldCapability(path=ptt, unsolicited_push=True),
        ),
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    result = scheduler.ensure_fresh(
        [freq, ptt],
        max_age=1.0,
        priority="user",
        reason="snapshot",
    )

    assert result.status is AcquisitionStatus.QUEUED
    requests = scheduler.pending_requests()
    assert len(requests) == 2
    assert not any(
        request.acquisition_method == "poll"
        and set(request.paths) == {freq, ptt}
        for request in requests
    )
    requests_by_path = {request.paths: request for request in requests}
    assert requests_by_path[(freq,)].acquisition_method == "poll"
    assert requests_by_path[(ptt,)].acquisition_method == "wait_for_unsolicited"


def test_policy_preferred_command_response_beats_polling_capability() -> None:
    clock = FreshnessClock(start=85.5)
    mode = FieldPath.active("main", "freq_mode", "mode")
    profile = RadioAcquisitionProfile(
        provider="x6200_like",
        capabilities=(
            FieldCapability(
                path=mode,
                polling=True,
                command_response_observable=True,
            ),
        ),
        field_policies={
            mode: AcquisitionPolicy(
                reconciliation_priority=ReconciliationPriority.COMMAND_RESPONSE,
            ),
        },
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    result = scheduler.ensure_fresh(
        mode,
        max_age=1.0,
        priority="reconciliation",
        reason="x6200-mode-reconcile",
    )

    assert result.status is AcquisitionStatus.QUEUED
    assert result.request is not None
    assert result.request.acquisition_method == "command_response"


def test_mixed_meter_and_frequency_request_preserves_meter_coalescing_policy() -> None:
    clock = FreshnessClock(start=86.0)
    meter = FieldPath.receiver("main", "meters", "s_meter")
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    profile = _profile(
        [meter, freq],
        field_policies={
            meter: AcquisitionPolicy(
                cadence_seconds=0.2,
                freshness_ttl_seconds=0.6,
                meter_coalescing=MeterCoalescingPolicy(window_seconds=0.1),
            ),
            freq: AcquisitionPolicy(
                cadence_seconds=5.0,
                freshness_ttl_seconds=15.0,
            ),
        },
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    result = scheduler.ensure_fresh(
        [meter, freq],
        max_age=1.0,
        priority="normal",
        reason="mixed-panel",
    )

    assert result.status is AcquisitionStatus.QUEUED
    requests = scheduler.pending_requests()
    assert len(requests) == 2
    requests_by_path = {request.paths: request for request in requests}
    meter_request = requests_by_path[(meter,)]
    freq_request = requests_by_path[(freq,)]
    assert meter_request.policy.meter_coalescing is not None
    assert meter_request.policy.meter_coalescing.window_seconds == 0.1
    assert freq_request.policy.meter_coalescing is None


def test_scheduler_output_can_return_observation_applied_through_state_store() -> None:
    clock = FreshnessClock(start=90.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    store = StateStore(freshness_clock=clock)
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)
    service = RadioStateModelService(store=store, scheduler=scheduler, clock=clock)

    result = service.ensure_fresh(
        freq,
        max_age=1.0,
        priority="user",
        reason="initial-snapshot",
    )
    assert result.request is not None

    change = store.apply(
        Observation(
            path=result.request.paths[0],
            value=14_074_000,
            source=SourceMetadata(
                source="poll_response",
                provider=result.request.provider,
                transport="fake",
                capability_id=result.request.capability_ids[0],
            ),
            timestamp_monotonic=clock.now(),
            max_age=result.request.max_age,
        )
    )

    assert change.revision == 1
    assert store.snapshot().field(freq).value == 14_074_000


def test_model_service_queues_when_field_observation_max_age_expired_without_mark_stale() -> (
    None
):
    clock = FreshnessClock(start=100.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    store = StateStore(freshness_clock=clock)
    store.apply(_observation(freq, 14_074_000, at=clock.now(), max_age=1.0))
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)
    service = RadioStateModelService(store=store, scheduler=scheduler, clock=clock)
    clock.advance(1.1)

    result = service.ensure_fresh(
        freq,
        max_age=10.0,
        priority="user",
        reason="snapshot",
    )

    assert result.status is AcquisitionStatus.QUEUED
    assert result.fields == ()
    assert result.request is not None
    assert result.request.paths == (freq,)


def test_stale_unsolicited_field_queues_reconciliation_and_accepts_readback() -> None:
    clock = FreshnessClock(start=110.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    store = StateStore(freshness_clock=clock)
    scheduler = AcquisitionScheduler(profile=_profile([freq]), clock=clock)
    service = RadioStateModelService(store=store, scheduler=scheduler, clock=clock)

    store.apply(_observation(freq, 14_074_000, at=clock.now(), max_age=0.5))
    clock.advance(0.6)

    stale = store.mark_stale_due()
    queued = service.ensure_fresh(
        freq,
        max_age=0.5,
        priority="reconciliation",
        reason="missed-unsolicited",
    )
    reconciled = store.apply(_observation(freq, 14_076_000, at=clock.now(), max_age=0.5))

    assert stale.changes == ()
    assert stale.reconciliation_requests[0].path == freq
    assert queued.status is AcquisitionStatus.QUEUED
    assert queued.request is not None
    assert queued.request.paths == (freq,)
    assert reconciled.revision == 2
    assert store.snapshot().field(freq).freshness is FreshnessState.FRESH
    assert store.snapshot().field(freq).value == 14_076_000


def test_due_polling_emits_only_pollable_cadence_fields_and_groups_by_existing_key() -> (
    None
):
    clock = FreshnessClock(start=200.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    mode = FieldPath.active("main", "freq_mode", "mode")
    ptt = FieldPath.global_("tx_state", "ptt")
    profile = RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(
            FieldCapability(path=freq, polling=True, unsolicited_push=True),
            FieldCapability(path=mode, polling=True),
            FieldCapability(path=ptt, unsolicited_push=True),
        ),
        default_policy=AcquisitionPolicy(
            cadence_seconds=2.0,
            freshness_ttl_seconds=6.0,
        ),
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    requests = scheduler.due_requests()

    assert len(requests) == 1
    assert requests[0].paths == (freq, mode)
    assert requests[0].priority is AcquisitionPriority.BACKGROUND
    assert requests[0].reason == "policy-cadence"
    assert requests[0].acquisition_method == "poll"
    assert scheduler.pending_requests() == requests


def test_due_polling_does_not_reemit_pending_cadence_request() -> None:
    clock = FreshnessClock(start=202.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    scheduler = AcquisitionScheduler(
        profile=_profile(
            [freq],
            default_policy=AcquisitionPolicy(cadence_seconds=1.0),
        ),
        clock=clock,
    )

    first = scheduler.due_requests(now=clock.now())
    second = scheduler.due_requests(now=clock.now())

    assert len(first) == 1
    assert first[0].paths == (freq,)
    assert second == ()
    assert scheduler.pending_requests() == first


def test_due_polling_skips_unsupported_unhooked_and_unsolicited_only_fields() -> None:
    clock = FreshnessClock(start=205.0)
    supported = FieldPath.active("main", "freq_mode", "freq_hz")
    unsupported = FieldPath.global_("tx_state", "power_on")
    unhooked = FieldPath.global_("health", "state")
    unsolicited_only = FieldPath.global_("tx_state", "ptt")
    profile = RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(
            FieldCapability(path=supported, polling=True),
            FieldCapability(
                path=unsupported,
                availability=FieldAvailability.UNSUPPORTED,
                diagnostic="not exposed",
            ),
            FieldCapability(path=unhooked),
            FieldCapability(path=unsolicited_only, unsolicited_push=True),
        ),
        default_policy=AcquisitionPolicy(cadence_seconds=1.0),
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    requests = scheduler.due_requests()

    assert len(requests) == 1
    assert requests[0].paths == (supported,)


def test_unchanged_acquisition_result_decays_cadence_exponentially_and_caps() -> None:
    clock = FreshnessClock(start=210.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    policy = AcquisitionPolicy(
        cadence_seconds=2.0,
        freshness_ttl_seconds=10.0,
        adaptive_decay=AdaptiveDecayPolicy(
            enabled=True,
            idle_multiplier=2.0,
            max_cadence_seconds=6.0,
        ),
    )
    scheduler = AcquisitionScheduler(
        profile=_profile([freq], default_policy=policy),
        clock=clock,
    )

    first = scheduler.due_requests()[0]
    scheduler.record_acquisition_result(first, _changeset(at=clock.now()))

    diagnostics = scheduler.diagnostics()
    assert diagnostics["cadenceByPath"][str(freq)]["currentCadenceSeconds"] == 4.0
    assert diagnostics["cadenceByPath"][str(freq)]["nextDueMonotonic"] == 214.0
    clock.advance(3.9)
    assert scheduler.due_requests() == ()
    clock.advance(0.1)

    second = scheduler.due_requests()[0]
    scheduler.record_acquisition_result(second, _changeset(at=clock.now()))

    diagnostics = scheduler.diagnostics()
    assert diagnostics["cadenceByPath"][str(freq)]["currentCadenceSeconds"] == 6.0
    assert diagnostics["cadenceByPath"][str(freq)]["nextDueMonotonic"] == 220.0


def test_semantic_change_resets_adaptive_cadence_to_base_policy() -> None:
    clock = FreshnessClock(start=220.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    policy = AcquisitionPolicy(
        cadence_seconds=2.0,
        freshness_ttl_seconds=10.0,
        adaptive_decay=AdaptiveDecayPolicy(
            enabled=True,
            idle_multiplier=2.0,
            max_cadence_seconds=8.0,
        ),
    )
    scheduler = AcquisitionScheduler(
        profile=_profile([freq], default_policy=policy),
        clock=clock,
    )
    first = scheduler.due_requests()[0]
    scheduler.record_acquisition_result(first, _changeset(at=clock.now()))
    clock.advance(4.0)

    second = scheduler.due_requests()[0]
    scheduler.record_acquisition_result(
        second,
        _changeset(
            changes=(FieldChange(path=freq, previous=7_074_000, current=14_074_000),),
            at=clock.now(),
        ),
    )

    diagnostics = scheduler.diagnostics()
    assert diagnostics["cadenceByPath"][str(freq)]["currentCadenceSeconds"] == 2.0
    assert diagnostics["cadenceByPath"][str(freq)]["nextDueMonotonic"] == 226.0
    clock.advance(1.9)
    assert scheduler.due_requests() == ()
    clock.advance(0.1)
    assert scheduler.due_requests()[0].paths == (freq,)


def test_diagnostics_include_cadence_next_due_pending_pressure_and_counts() -> None:
    clock = FreshnessClock(start=230.0)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    meter = FieldPath.receiver("main", "meters", "s_meter")
    profile = _profile(
        [freq, meter],
        field_policies={
            meter: AcquisitionPolicy(
                external_cat_pause=ExternalCatPauseBehavior.CONTINUE,
            ),
        },
    )
    scheduler = AcquisitionScheduler(profile=profile, clock=clock)

    scheduler.due_requests()
    scheduler.pause_external_cat(owner="external")
    scheduler.ensure_fresh(
        freq,
        max_age=1.0,
        priority="user",
        reason="deferred-user-read",
    )

    diagnostics = scheduler.diagnostics()

    assert diagnostics["queuedRequestCount"] == 1
    assert diagnostics["deferredRequestCount"] == 1
    assert diagnostics["cadenceByPath"][str(freq)]["baseCadenceSeconds"] == 5.0
    assert diagnostics["cadenceByPath"][str(freq)]["nextDueMonotonic"] == 230.0
    assert diagnostics["requestPressureByPriorityFamily"]["background:meters"] == 1
    assert diagnostics["requestPressureByPriorityFamily"]["user:freq_mode"] == 1


def test_meter_coalescing_latest_sample_wins_and_exposes_drop_counts() -> None:
    clock = FreshnessClock(start=240.0)
    store = StateStore(freshness_clock=clock)
    meter = FieldPath.receiver("main", "meters", "s_meter")
    policy = MeterCoalescingPolicy(window_seconds=0.2, max_samples=2)
    coalescer = MeterObservationCoalescer()

    coalescer.record(_observation(meter, 40, at=clock.now()), policy)
    clock.advance(0.05)
    coalescer.record(_observation(meter, 41, at=clock.now()), policy)
    clock.advance(0.05)
    coalescer.record(_observation(meter, 42, at=clock.now()), policy)

    assert coalescer.flush_due(store, now=clock.now() + 0.14) is None
    changeset = coalescer.flush_due(store, now=clock.now() + 0.2)

    assert changeset is not None
    assert changeset.coalesced is True
    assert changeset.changes[0].current == 42
    assert store.snapshot().field(meter).value == 42
    assert store.snapshot().state_revision == 1
    diagnostics = coalescer.diagnostics()
    assert diagnostics["droppedSampleCount"] == 1
    assert diagnostics["coalescedSampleCount"] == 1
    assert diagnostics["pendingSampleCount"] == 0


def test_meter_coalescing_flush_uses_latest_batch_timestamp_not_last_path() -> None:
    clock = FreshnessClock(start=250.0)
    store = StateStore(freshness_clock=clock)
    power = FieldPath.receiver("main", "meters", "power")
    s_meter = FieldPath.receiver("main", "meters", "s_meter")
    policy = MeterCoalescingPolicy(window_seconds=0.1)
    coalescer = MeterObservationCoalescer()

    coalescer.record(_observation(s_meter, 3, at=10.0), policy)
    coalescer.record(_observation(power, 40, at=10.5), policy)

    changeset = coalescer.flush(store)

    assert changeset is not None
    assert changeset.coalesced is True
    assert changeset.timestamp_monotonic == 10.5
    assert changeset.revision == 2
    assert changeset.observation_seq == 2
    assert {change.path for change in changeset.changes} == {power, s_meter}


def test_meter_coalescing_flush_due_leaves_longer_window_samples_pending() -> None:
    clock = FreshnessClock(start=260.0)
    store = StateStore(freshness_clock=clock)
    power = FieldPath.receiver("main", "meters", "power")
    s_meter = FieldPath.receiver("main", "meters", "s_meter")
    short_policy = MeterCoalescingPolicy(window_seconds=0.1)
    long_policy = MeterCoalescingPolicy(window_seconds=1.0)
    coalescer = MeterObservationCoalescer()

    coalescer.record(_observation(power, 40, at=clock.now()), short_policy)
    coalescer.record(_observation(s_meter, 3, at=clock.now()), long_policy)

    first = coalescer.flush_due(store, now=clock.now() + 0.1)

    assert first is not None
    assert [change.path for change in first.changes] == [power]
    assert store.snapshot().field(power).value == 40
    diagnostics = coalescer.diagnostics()
    assert diagnostics["pendingSampleCount"] == 1
    assert diagnostics["pendingPaths"] == [str(s_meter)]

    second = coalescer.flush_due(store, now=clock.now() + 1.0)

    assert second is not None
    assert [change.path for change in second.changes] == [s_meter]
    assert store.snapshot().field(s_meter).value == 3
    assert coalescer.diagnostics()["pendingSampleCount"] == 0
