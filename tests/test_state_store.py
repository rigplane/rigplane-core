"""Runtime StateStore behavior for the radio state pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
    StateFreshnessService,
)
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_store import (
    FreshnessClock,
    FreshnessState,
    StateSnapshot,
    StateStore,
)


def _source() -> SourceMetadata:
    return SourceMetadata(
        source="poll_response",
        provider="test",
        transport="fake",
        native_id="meter",
    )


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


def _acquisition_profile(*paths: FieldPath) -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=tuple(
            FieldCapability(
                path=path,
                polling=True,
                command_response_observable=True,
            )
            for path in paths
        ),
        default_policy=AcquisitionPolicy(),
    )


def test_noop_observations_do_not_advance_state_revision() -> None:
    store = StateStore()
    path = FieldPath.receiver("main", "meters", "s_meter")

    first = store.apply(_observation(path, 42, at=1.0, max_age=1.0))
    second = store.apply(_observation(path, 42, at=1.2, max_age=1.0))

    assert first.revision == 1
    assert first.freshness_revision == 1
    assert first.observation_seq == 1
    assert len(first.changes) == 1
    assert second.revision == 1
    assert second.freshness_revision == 1
    assert second.observation_seq == 2
    assert second.changes == ()
    assert store.snapshot().state_revision == 1
    assert store.snapshot().observation_seq == 2


def test_freshness_expiration_advances_freshness_without_state_change() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    path = FieldPath.receiver("main", "meters", "s_meter")
    store.apply(_observation(path, 42, at=clock.now(), max_age=1.0))
    baseline = store.snapshot()

    clock.advance(1.1)
    delta = store.mark_stale_due()
    snapshot = store.snapshot()

    assert snapshot.state_revision == baseline.state_revision
    assert snapshot.freshness_revision == baseline.freshness_revision + 1
    assert snapshot.field(path).value == 42
    assert snapshot.field(path).freshness == FreshnessState.STALE
    assert delta.changes == ()
    assert delta.freshness[0].previous is FreshnessState.FRESH
    assert delta.freshness[0].current is FreshnessState.STALE


def test_full_snapshot_and_delta_projection_agree_after_observation_sequence() -> None:
    clock = FreshnessClock(start=0.0)
    store = StateStore(freshness_clock=clock)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    mode = FieldPath.active("main", "freq_mode", "mode")

    store.apply(_observation(freq, 14_074_000, at=0.0, max_age=10.0))
    store.apply(_observation(mode, "USB-D", at=0.1, max_age=10.0))
    store.apply(_observation(freq, 14_074_000, at=0.2, max_age=10.0))
    store.apply(_observation(freq, 14_075_000, at=0.3, max_age=10.0))

    snapshot = store.snapshot()
    delta = store.delta_since(StateSnapshot.empty())
    projected_values: dict[FieldPath, Any] = {}
    for change in delta.changes:
        projected_values[change.path] = change.current

    assert projected_values == {field.path: field.value for field in snapshot.fields}
    assert delta.state_revision == snapshot.state_revision
    assert delta.freshness_revision == snapshot.freshness_revision
    assert delta.observation_seq == snapshot.observation_seq


def test_snapshot_output_cannot_mutate_store_owned_state() -> None:
    store = StateStore()
    path = FieldPath.global_("health", "state")
    payload = {"nested": ["initial"]}

    store.apply(_observation(path, payload, at=1.0))
    payload["nested"].append("external")
    exported = store.snapshot().as_dict()
    exported[str(path)]["value"]["nested"].append("snapshot")

    assert store.snapshot().field(path).value == {"nested": ["initial"]}


def test_returned_changes_cannot_mutate_delta_history() -> None:
    store = StateStore()
    path = FieldPath.global_("health", "state")
    payload = {"nested": ["initial"]}

    changeset = store.apply(_observation(path, payload, at=1.0))
    changeset.changes[0].current["nested"].append("changeset")
    delta = store.delta_since(StateSnapshot.empty())
    delta.changes[0].current["nested"].append("delta")

    assert store.delta_since(StateSnapshot.empty()).changes[0].current == {
        "nested": ["initial"]
    }
    assert store.snapshot().field(path).value == {"nested": ["initial"]}


def test_direct_writer_api_is_not_exposed() -> None:
    public_callables = {
        name
        for name in dir(StateStore)
        if not name.startswith("_") and callable(getattr(StateStore, name))
    }

    assert {"apply", "delta_since", "mark_stale_due", "snapshot"} <= public_callables
    assert public_callables.isdisjoint({"set", "update", "mutate", "write"})


def test_dropped_event_marks_stale_and_requests_reconciliation() -> None:
    clock = FreshnessClock(start=20.0)
    store = StateStore(freshness_clock=clock)
    path = FieldPath.global_("tx_state", "ptt")
    store.apply(_observation(path, False, at=clock.now(), max_age=0.5))

    clock.advance(0.6)
    delta = store.mark_stale_due()

    assert store.snapshot().field(path).freshness == FreshnessState.STALE
    assert delta.reconciliation_requests
    assert delta.reconciliation_requests[0].path == path
    assert delta.reconciliation_requests[0].reason == "stale"
    assert delta.reconciliation_requests[0].state_revision == 1
    assert delta.reconciliation_requests[0].freshness_revision == 2


def test_freshness_service_marks_stale_and_queues_reconciliation_without_web() -> None:
    clock = FreshnessClock(start=50.0)
    store = StateStore(freshness_clock=clock)
    path = FieldPath.global_("tx_state", "ptt")
    scheduler = AcquisitionScheduler(
        profile=_acquisition_profile(path),
        clock=clock,
    )
    service = StateFreshnessService(store=store, scheduler=scheduler)
    store.apply(_observation(path, False, at=clock.now(), max_age=0.5))

    clock.advance(0.6)
    delta = service.tick()

    assert store.snapshot().field(path).freshness is FreshnessState.STALE
    assert delta.reconciliation_requests[0].path == path
    requests = scheduler.pending_requests()
    assert len(requests) == 1
    assert requests[0].paths == (path,)
    assert requests[0].priority is AcquisitionPriority.RECONCILIATION
    assert requests[0].reason == "stale"


def test_freshness_decay_requires_wired_running_service_not_intrinsic() -> None:
    """MOR-432: decay is not intrinsic to the store — it requires a wired
    StateFreshnessService to be driven (here via tick()). A bare store left
    undriven over the same elapsed time keeps the field FRESH.
    """

    clock = FreshnessClock(start=80.0)
    path = FieldPath.global_("tx_state", "ptt")

    # Wired + driven: the service ticks the store and the field decays.
    wired_store = StateStore(freshness_clock=clock)
    scheduler = AcquisitionScheduler(
        profile=_acquisition_profile(path),
        clock=clock,
    )
    service = StateFreshnessService(store=wired_store, scheduler=scheduler)
    wired_store.apply(_observation(path, False, at=clock.now(), max_age=0.5))

    # Undriven bare store with the same observation and clock — no service runs
    # over it, so nothing calls mark_stale_due on it.
    bare_store = StateStore(freshness_clock=clock)
    bare_store.apply(_observation(path, False, at=80.0, max_age=0.5))

    clock.advance(0.6)
    service.tick()

    # Wired store decayed; bare undriven store did not (still FRESH).
    assert wired_store.snapshot().field(path).freshness is FreshnessState.STALE
    assert bare_store.snapshot().field(path).freshness is FreshnessState.FRESH


def test_observation_refreshes_stale_field_without_semantic_state_change() -> None:
    clock = FreshnessClock(start=30.0)
    store = StateStore(freshness_clock=clock)
    path = FieldPath.receiver("main", "meters", "s_meter")
    store.apply(_observation(path, 9, at=clock.now(), max_age=1.0))
    clock.advance(1.1)
    store.mark_stale_due()

    refreshed = store.apply(_observation(path, 9, at=clock.now(), max_age=1.0))

    assert refreshed.revision == 1
    assert refreshed.freshness_revision == 3
    assert refreshed.changes == ()
    assert store.snapshot().field(path).freshness == FreshnessState.FRESH


def test_meter_delta_is_visible_without_unrelated_follow_up_revision() -> None:
    clock = FreshnessClock(start=40.0)
    store = StateStore(freshness_clock=clock)
    freq = FieldPath.active("main", "freq_mode", "freq_hz")
    meter = FieldPath.receiver("main", "meters", "s_meter")

    store.apply(_observation(freq, 14_074_000, at=clock.now(), max_age=10.0))
    baseline = store.snapshot()
    store.apply(_observation(meter, 42, at=clock.now() + 0.1, max_age=0.5))

    delta = store.delta_since(baseline)

    assert delta.state_revision == 2
    assert delta.observation_seq == 2
    assert [(change.path, change.current) for change in delta.changes] == [(meter, 42)]


def test_history_prunes_old_deltas_while_recent_replay_still_works() -> None:
    store = StateStore(max_history_count=2)
    meter = FieldPath.receiver("main", "meters", "s_meter")

    store.apply(_observation(meter, 1, at=1.0))
    retained_baseline = store.snapshot()
    store.apply(_observation(meter, 2, at=2.0))
    store.apply(_observation(meter, 3, at=3.0))

    delta = store.delta_since(retained_baseline)

    assert len(store._history) == 2  # noqa: SLF001
    assert delta.requires_full_snapshot is False
    assert delta.state_revision == 3
    assert delta.freshness_revision == 1
    assert delta.observation_seq == 3
    assert [change.current for change in delta.changes] == [2, 3]


def test_replay_before_retention_floor_requires_full_snapshot() -> None:
    store = StateStore(max_history_count=2)
    meter = FieldPath.receiver("main", "meters", "s_meter")

    store.apply(_observation(meter, 1, at=1.0))
    store.apply(_observation(meter, 2, at=2.0))
    store.apply(_observation(meter, 3, at=3.0))

    delta = store.delta_since(StateSnapshot.empty())

    assert len(store._history) == 2  # noqa: SLF001
    assert delta.requires_full_snapshot is True
    assert delta.state_revision == 3
    assert delta.freshness_revision == 1
    assert delta.observation_seq == 3
    assert delta.changes == ()
    assert delta.freshness == ()
    assert delta.reconciliation_requests == ()
    assert delta.to_dict()["requiresFullSnapshot"] is True


def test_freshness_replay_before_retention_floor_requires_full_snapshot() -> None:
    clock = FreshnessClock(start=70.0)
    store = StateStore(freshness_clock=clock, max_history_count=1)
    meter = FieldPath.receiver("main", "meters", "s_meter")

    store.apply(_observation(meter, 1, at=clock.now(), max_age=0.5))
    stale_baseline = store.snapshot()
    clock.advance(0.6)
    store.mark_stale_due()
    store.apply(_observation(meter, 1, at=clock.now(), max_age=0.5))

    stale_delta = store.delta_since(stale_baseline)

    assert stale_delta.requires_full_snapshot is True
    assert stale_delta.state_revision == 1
    assert stale_delta.freshness_revision == 3
    assert stale_delta.observation_seq == 2
    assert stale_delta.freshness == ()


def test_freshness_clock_rejects_backwards_time() -> None:
    clock = FreshnessClock(start=3.0)

    with pytest.raises(ValueError, match="backwards"):
        clock.advance(-0.1)


def test_conflicting_sources_at_equal_freshness_resolve_last_writer_wins() -> None:
    """Two sources, same path, same timestamp, different values: last write wins.

    ``StateStore.apply`` unconditionally overwrites value and source for the
    path (state_store.py:275-281); there is no source-priority or freshness
    tie-break, so resolution is deterministic last-writer-by-observation-seq.
    """

    store = StateStore()
    path = FieldPath.receiver("main", "meters", "s_meter")
    poll_source = SourceMetadata(source="poll_response", provider="poller")
    civ_source = SourceMetadata(source="civ_unsolicited", provider="radio")

    first = store.apply(
        Observation(path=path, value=10, source=poll_source, timestamp_monotonic=5.0)
    )
    second = store.apply(
        Observation(path=path, value=20, source=civ_source, timestamp_monotonic=5.0)
    )

    field = store.snapshot().field(path)
    assert first.observation_seq == 1
    assert second.observation_seq == 2
    assert field.value == 20
    assert field.source == civ_source
