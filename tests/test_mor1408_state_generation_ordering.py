from __future__ import annotations

import copy
from typing import Any

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    LOCAL_MONOTONIC_CLOCK_DOMAIN,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateSnapshot, StateStore

_LOCAL_CLOCK = LOCAL_MONOTONIC_CLOCK_DOMAIN
_SOURCE = SourceMetadata(
    source="poll_response",
    provider="mor1408_test",
    transport="fake",
    native_id="sample",
)


def _observation(
    path: FieldPath,
    value: Any,
    *,
    at: float,
    generation: int | None,
    clock_domain: str | None = _LOCAL_CLOCK,
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_SOURCE,
        timestamp_monotonic=at,
        provider_generation=generation,
        clock_domain=clock_domain,
        max_age=30.0,
    )


def _relative_pair(*, generation: int, at: float = 10.0) -> tuple[Observation, ...]:
    specs = (
        (FieldPath.active("0", "freq_mode", "freq_hz"), 14_284_000, 0.0),
        (FieldPath.active("0", "freq_mode", "mode"), "USB", 0.1),
        (FieldPath.unselected("0", "freq_mode", "freq_hz"), 14_075_000, 0.2),
        (FieldPath.unselected("0", "freq_mode", "mode"), "USB", 0.3),
    )
    return tuple(
        _observation(path, value, at=at + offset, generation=generation)
        for path, value, offset in specs
    )


def _configure_relative(store: StateStore, generation: int) -> None:
    store.configure_relative_vfo_retention(
        generation=generation, max_age=30.0, coherence_window=2.0
    )


def _mutation_surface(store: StateStore) -> tuple[object, ...]:
    retention = store._relative_vfo_retention  # noqa: SLF001
    snapshot = store.snapshot().to_dict()
    snapshot.pop("generatedAtMonotonic")
    return (
        snapshot,
        copy.deepcopy(store._history),  # noqa: SLF001
        store._history_floor_state_revision,  # noqa: SLF001
        store._history_floor_freshness_revision,  # noqa: SLF001
        None if retention is None else copy.deepcopy(retention.pending),
        None if retention is None else retention.expires_at,
        None if retention is None else retention.generation,
    )


def test_begin_provider_generation_atomically_invalidates_all_live_and_relative_state() -> (
    None
):
    store = StateStore()
    external_epoch = 41
    token = store.provider_generation
    _configure_relative(store, external_epoch)
    store.apply_relative_vfo_observations(
        _relative_pair(generation=token), generation=external_epoch
    )
    store.apply_current(
        _observation(
            FieldPath.active_slot("0"),
            "A",
            at=10.4,
            generation=None,
        )
    )
    before = store.snapshot()
    before_history_size = len(store._history)  # noqa: SLF001

    next_token = store.begin_provider_generation()
    after = store.snapshot()

    assert next_token != token
    assert after.provider_generation == next_token
    assert after.fields == ()
    assert after.state_revision == before.state_revision + 1
    assert after.freshness_revision == before.freshness_revision
    assert after.observation_seq == before.observation_seq
    assert len(store._history) == before_history_size + 1  # noqa: SLF001
    retention = store._relative_vfo_retention  # noqa: SLF001
    assert retention is not None
    assert retention.pending == {}
    assert retention.expires_at is None
    assert store.delta_since(before).requires_full_snapshot is True


def test_old_generation_generic_observation_mutates_no_counter_history_or_provenance() -> (
    None
):
    store = StateStore()
    old_token = store.provider_generation
    current_token = store.begin_provider_generation()
    path = FieldPath.receiver("main", "meters", "s_meter")
    store.apply(_observation(path, 42, at=20.0, generation=current_token))
    before = _mutation_surface(store)

    rejected = store.apply(_observation(path, 99, at=30.0, generation=old_token))

    assert rejected.changes == ()
    assert rejected.observed_paths == ()
    assert _mutation_surface(store) == before


def test_old_generation_relative_batch_mutates_no_pending_tuple_alias_or_expiry() -> (
    None
):
    store = StateStore()
    external_epoch = 9
    old_token = store.provider_generation
    _configure_relative(store, external_epoch)
    current_token = store.begin_provider_generation()
    store.apply_relative_vfo_observations(
        _relative_pair(generation=current_token), generation=external_epoch
    )
    before = _mutation_surface(store)

    rejected = store.apply_relative_vfo_observations(
        _relative_pair(generation=old_token, at=40.0), generation=external_epoch
    )

    assert rejected.changes == ()
    assert rejected.observed_paths == ()
    assert _mutation_surface(store) == before


def test_new_generation_accepts_numerically_lower_timestamp() -> None:
    store = StateStore()
    path = FieldPath.receiver("main", "meters", "s_meter")
    store.apply(_observation(path, 10, at=1000.0, generation=store.provider_generation))
    token = store.begin_provider_generation()

    accepted = store.apply(_observation(path, 20, at=1.0, generation=token))

    assert accepted.changes[0].current == 20
    assert store.snapshot().field(path).last_observed_monotonic == 1.0


def test_strictly_older_same_generation_same_local_clock_domain_is_rejected() -> None:
    store = StateStore()
    token = store.provider_generation
    path = FieldPath.receiver("main", "meters", "s_meter")
    store.apply(_observation(path, 20, at=20.0, generation=token))
    before = _mutation_surface(store)

    rejected = store.apply(_observation(path, 10, at=10.0, generation=token))

    assert rejected.changes == ()
    assert rejected.observed_paths == ()
    assert _mutation_surface(store) == before


def test_different_or_unknown_clock_domains_are_not_numerically_ordered() -> None:
    store = StateStore()
    token = store.provider_generation
    path = FieldPath.receiver("main", "meters", "s_meter")
    store.apply(
        _observation(path, 20, at=20.0, generation=token, clock_domain="clock-a")
    )

    different = store.apply(
        _observation(path, 10, at=10.0, generation=token, clock_domain="clock-b")
    )
    unknown = store.apply(
        _observation(path, 5, at=5.0, generation=token, clock_domain=None)
    )

    assert different.changes[0].current == 10
    assert unknown.changes[0].current == 5
    assert store.snapshot().field(path).value == 5


def test_missing_token_is_rejected_by_async_apply_and_apply_current_is_explicit() -> (
    None
):
    store = StateStore()
    path = FieldPath.global_("connection", "connected")
    observation = _observation(path, True, at=1.0, generation=None)
    before = _mutation_surface(store)

    rejected = store.apply(observation)

    assert rejected.changes == ()
    assert _mutation_surface(store) == before
    accepted = store.apply_current(observation)
    assert accepted.changes[0].current is True
    assert store.snapshot().field(path).provider_generation == store.provider_generation


def test_snapshot_and_delta_carry_store_generation_not_external_epoch() -> None:
    store = StateStore()
    token = store.begin_provider_generation()
    external_epoch = 88
    _configure_relative(store, external_epoch)
    baseline = StateSnapshot.empty()
    store.apply_relative_vfo_observations(
        _relative_pair(generation=token), generation=external_epoch
    )

    snapshot = store.snapshot()
    delta = store.delta_since(baseline)

    assert snapshot.provider_generation == token
    assert {field.provider_generation for field in snapshot.fields} == {token}
    assert delta.provider_generation == token
    assert snapshot.to_dict()["providerGeneration"] == token
    assert delta.to_dict()["providerGeneration"] == token


def test_bootstrap_default_zero_is_fixed_and_never_late_bound() -> None:
    store = StateStore()
    path = FieldPath.global_("connection", "ready")
    bootstrap = Observation(
        path=path,
        value=True,
        source=SourceMetadata(source="test", provider="mor1408_test"),
        timestamp_monotonic=1.0,
    )
    assert bootstrap.provider_generation == 0
    store.apply(bootstrap)
    token = store.begin_provider_generation()
    before = _mutation_surface(store)

    default_zero_created_after_advance = Observation(
        path=path,
        value=False,
        source=bootstrap.source,
        timestamp_monotonic=2.0,
    )
    missing = Observation(
        path=path,
        value=False,
        source=bootstrap.source,
        timestamp_monotonic=2.0,
        provider_generation=None,
    )

    assert default_zero_created_after_advance.provider_generation == 0
    assert store.apply(default_zero_created_after_advance).changes == ()
    assert store.apply(missing).changes == ()
    assert _mutation_surface(store) == before
    accepted = store.apply_current(missing)
    assert accepted.changes[0].current is False
    assert store.snapshot().field(path).provider_generation == token
