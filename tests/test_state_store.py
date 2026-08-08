"""Runtime StateStore behavior for the radio state pipeline."""

from __future__ import annotations

from dataclasses import replace
from itertools import permutations
from typing import Any

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
    VfoSlot,
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
    source: str = "poll_response",
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=SourceMetadata(
            source=source,
            provider="test",
            transport="fake",
            native_id="relative-vfo",
        ),
        timestamp_monotonic=at,
        max_age=max_age,
    )


def _relative_vfo_observations(
    *,
    at: float,
    selected_freq: int = 14_284_000,
    selected_mode: str = "USB",
    unselected_freq: int = 14_075_000,
    unselected_mode: str = "USB",
) -> tuple[Observation, ...]:
    return (
        _observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            selected_freq,
            at=at,
            max_age=5.0,
        ),
        _observation(
            FieldPath.active("0", "freq_mode", "mode"),
            selected_mode,
            at=at + 0.2,
            max_age=5.0,
        ),
        _observation(
            FieldPath.active("0", "freq_mode", "filter_num"),
            1,
            at=at + 0.2,
        ),
        _observation(
            FieldPath.active("0", "freq_mode", "data_mode"),
            0,
            at=at + 0.2,
        ),
        _observation(
            FieldPath.unselected("0", "freq_mode", "freq_hz"),
            unselected_freq,
            at=at + 0.4,
            max_age=5.0,
        ),
        _observation(
            FieldPath.unselected("0", "freq_mode", "mode"),
            unselected_mode,
            at=at + 0.6,
            max_age=5.0,
        ),
        _observation(
            FieldPath.unselected("0", "freq_mode", "filter_num"),
            1,
            at=at + 0.6,
        ),
        _observation(
            FieldPath.unselected("0", "freq_mode", "data_mode"),
            0,
            at=at + 0.6,
        ),
    )


def _relative_vfo_deadline(store: StateStore) -> float:
    return max(
        field.last_observed_monotonic + field.max_age
        for field in store.snapshot().fields
        if field.max_age is not None
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


def test_relative_vfo_bootstrap_is_atomic_then_live_leaves_patch_immediately() -> None:
    clock = FreshnessClock(start=100.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=7,
        max_age=31.0,
        coherence_window=5.0,
    )
    base = _relative_vfo_observations(at=100.0)

    for observation in base[:5]:
        store.apply_relative_vfo_observations((observation,), generation=7)
        assert store.snapshot().fields == ()

    store.apply_relative_vfo_observations(base[5:], generation=7)
    committed = store.snapshot()
    assert committed.field("receiver.0.active.freq_mode.freq_hz").value == 14_284_000
    assert (
        committed.field("receiver.0.unselected.freq_mode.freq_hz").value == 14_075_000
    )

    knob_source = SourceMetadata(
        source="civ_unsolicited",
        provider="test",
        transport="fake",
        native_id="knob-frequency",
    )
    knob = Observation(
        path=FieldPath.active("0", "freq_mode", "freq_hz"),
        value=14_285_000,
        source=knob_source,
        timestamp_monotonic=101.0,
        max_age=5.0,
    )
    store.apply_relative_vfo_observations((knob,), generation=7)

    patched = store.snapshot()
    selected = patched.field("receiver.0.active.freq_mode.freq_hz")
    assert selected.value == 14_285_000
    assert selected.last_observed_monotonic == 101.0
    assert selected.source == knob_source
    assert patched.field("receiver.0.active.freq_mode.mode").value == "USB"
    assert patched.field("receiver.0.unselected.freq_mode.freq_hz").value == 14_075_000
    assert _relative_vfo_deadline(store) == pytest.approx(131.6)

    live_mode = (
        _observation(
            FieldPath.active("0", "freq_mode", "mode"),
            "LSB",
            at=101.1,
            max_age=5.0,
            source="civ_unsolicited",
        ),
        _observation(
            FieldPath.active("0", "freq_mode", "filter_num"),
            2,
            at=101.1,
            source="civ_unsolicited",
        ),
        _observation(
            FieldPath.active("0", "freq_mode", "data_mode"),
            1,
            at=101.1,
            source="civ_unsolicited",
        ),
    )
    store.apply_relative_vfo_observations(live_mode, generation=7)
    patched = store.snapshot()
    assert patched.field("receiver.0.active.freq_mode.mode").value == "LSB"
    assert patched.field("receiver.0.active.freq_mode.filter_num").value == 2
    assert patched.field("receiver.0.active.freq_mode.data_mode").value == 1
    assert _relative_vfo_deadline(store) == pytest.approx(131.6)


@pytest.mark.parametrize("active_slot", ["A", "B"])
@pytest.mark.parametrize("source", ["civ_unsolicited", "command_response"])
def test_bound_relative_vfo_leaf_updates_matching_absolute_alias_atomically(
    active_slot: str,
    source: str,
) -> None:
    clock = FreshnessClock(start=100.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=7,
        max_age=31.0,
        coherence_window=5.0,
    )
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=100.0), generation=7
    )
    store.apply(_observation(FieldPath.active_slot("0"), active_slot, at=100.1))

    selected_slot = active_slot
    unselected_slot = "B" if active_slot == "A" else "A"
    for relative, absolute, values in (
        ("active", selected_slot, (14_130_000, "LSB", 2, 1)),
        ("unselected", unselected_slot, (14_076_000, "CW", 3, 1)),
    ):
        paths = tuple(
            (
                FieldPath.active("0", "freq_mode", name)
                if relative == "active"
                else FieldPath.unselected("0", "freq_mode", name)
            )
            for name in ("freq_hz", "mode", "filter_num", "data_mode")
        )
        observations = tuple(
            _observation(path, value, at=101.0, max_age=5.0, source=source)
            for path, value in zip(paths, values, strict=True)
        )
        before = store.snapshot()
        changeset = store.apply_relative_vfo_observations(observations, generation=7)
        after = store.snapshot()

        assert changeset.revision == after.state_revision
        assert after.state_revision == before.state_revision + 8
        for path, value in zip(paths, values, strict=True):
            relative_field = after.field(path)
            alias_path = FieldPath.vfo_slot("0", absolute, "freq_mode", path.name)
            alias_field = after.field(alias_path)
            assert alias_field.value == value == relative_field.value
            assert alias_field.source == relative_field.source
            assert (
                alias_field.last_observed_monotonic
                == relative_field.last_observed_monotonic
            )
            assert alias_field.max_age == relative_field.max_age
            assert alias_path in changeset.observed_paths
            assert path in changeset.observed_paths

    snapshot = store.snapshot()
    assert (
        snapshot.field(
            FieldPath.vfo_slot("0", selected_slot, "freq_mode", "freq_hz")
        ).value
        == 14_130_000
    )
    assert (
        snapshot.field(
            FieldPath.vfo_slot("0", unselected_slot, "freq_mode", "freq_hz")
        ).value
        == 14_076_000
    )


@pytest.mark.parametrize(
    ("radio_frequency", "command_steps"),
    (
        (14_190_000, (14_185_000, 14_170_000, 14_150_000, 14_130_000)),
        (14_210_000, (14_205_000, 14_195_000, 14_185_000, 14_180_000)),
    ),
)
def test_web_command_frequency_trace_updates_bound_alias_without_civ_catchup(
    radio_frequency: int,
    command_steps: tuple[int, ...],
) -> None:
    store = StateStore()
    store.configure_relative_vfo_retention(
        generation=1,
        max_age=30.0,
        coherence_window=5.0,
    )
    base = _relative_vfo_observations(at=10.0, selected_freq=radio_frequency)
    store.apply_relative_vfo_observations(base, generation=1)
    store.apply(_observation(FieldPath.active_slot("0"), "A", at=10.7))
    store.apply_relative_vfo_observations(base, generation=1)
    sibling = store.snapshot().field(
        FieldPath.vfo_slot("0", "B", "freq_mode", "freq_hz")
    )
    path = FieldPath.active("0", "freq_mode", "freq_hz")
    alias_path = FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz")
    for index, commanded_frequency in enumerate(command_steps):
        observation = Observation(
            path=path,
            value=commanded_frequency,
            source=SourceMetadata(
                source="command_response",
                provider="web_command",
                transport="websocket",
                native_id="spectrum_drag",
                command_source="websocket",
                session_id="hardware-trace",
            ),
            timestamp_monotonic=11.0 + index / 10,
            quality=("confirmed",),
            correlation_id=f"drag-command-{index}",
        )

        before = store.snapshot()
        changeset = store.apply(observation)
        after = store.snapshot()
        relative = after.field(path)
        alias = after.field(alias_path)

        assert changeset.revision == after.state_revision == before.state_revision + 2
        assert {change.path for change in changeset.changes} == {path, alias_path}
        assert set(changeset.observed_paths) == {path, alias_path}
        assert relative.value == alias.value == commanded_frequency
        assert relative.source == alias.source == observation.source
        assert (
            relative.last_observed_monotonic
            == alias.last_observed_monotonic
            == observation.timestamp_monotonic
        )
        assert relative.quality == alias.quality == observation.quality
        assert relative.max_age == alias.max_age
        assert after.field(sibling.path) == sibling


@pytest.mark.parametrize("active_slot", ["A", "B"])
@pytest.mark.parametrize("relative_slot", [VfoSlot.ACTIVE, VfoSlot.UNSELECTED])
def test_direct_relative_apply_projects_every_leaf_to_current_bound_alias(
    active_slot: str,
    relative_slot: VfoSlot,
) -> None:
    store = StateStore()
    store.configure_relative_vfo_retention(
        generation=4,
        max_age=30.0,
        coherence_window=5.0,
    )
    base = _relative_vfo_observations(at=20.0)
    store.apply_relative_vfo_observations(base, generation=4)
    store.apply(_observation(FieldPath.active_slot("0"), active_slot, at=20.7))
    store.apply_relative_vfo_observations(base, generation=4)
    unselected_slot = "B" if active_slot == "A" else "A"
    alias_slot = active_slot if relative_slot is VfoSlot.ACTIVE else unselected_slot
    sibling_slot = unselected_slot if relative_slot is VfoSlot.ACTIVE else active_slot

    for index, (name, value) in enumerate(
        (("freq_hz", 14_181_000), ("mode", "LSB"), ("filter_num", 2), ("data_mode", 1))
    ):
        path = (
            FieldPath.active("0", "freq_mode", name)
            if relative_slot is VfoSlot.ACTIVE
            else FieldPath.unselected("0", "freq_mode", name)
        )
        alias_path = FieldPath.vfo_slot("0", alias_slot, "freq_mode", name)
        sibling_path = FieldPath.vfo_slot("0", sibling_slot, "freq_mode", name)
        sibling = store.snapshot().field(sibling_path)
        observation = Observation(
            path=path,
            value=value,
            source=SourceMetadata(
                source="command_response",
                provider="web_command",
                transport="websocket",
                native_id=f"set_{name}",
                command_source="websocket",
                session_id="leaf-matrix",
            ),
            timestamp_monotonic=21.0 + index,
            quality=("confirmed", "authoritative"),
            correlation_id=f"command-{name}",
        )

        before = store.snapshot()
        changeset = store.apply(observation)
        after = store.snapshot()
        relative = after.field(path)
        alias = after.field(alias_path)

        assert changeset.revision == after.state_revision == before.state_revision + 2
        assert {change.path for change in changeset.changes} == {path, alias_path}
        assert set(changeset.observed_paths) == {path, alias_path}
        assert relative.value == alias.value == value
        assert relative.source == alias.source == observation.source
        assert (
            relative.last_observed_monotonic
            == alias.last_observed_monotonic
            == observation.timestamp_monotonic
        )
        assert relative.quality == alias.quality == observation.quality
        assert relative.max_age == alias.max_age
        assert after.field(sibling_path) == sibling


def test_direct_relative_apply_without_observed_identity_does_not_invent_alias() -> (
    None
):
    store = StateStore()
    store.configure_relative_vfo_retention(
        generation=2,
        max_age=30.0,
        coherence_window=5.0,
    )
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=30.0), generation=2
    )
    path = FieldPath.active("0", "freq_mode", "freq_hz")

    changeset = store.apply(
        _observation(
            path,
            14_182_000,
            at=31.0,
            source="command_response",
        )
    )

    assert {change.path for change in changeset.changes} == {path}
    assert changeset.observed_paths == (path,)
    snapshot_paths = {field.path for field in store.snapshot().fields}
    assert not any(item.slot in {VfoSlot.A, VfoSlot.B} for item in snapshot_paths)


def test_bound_relative_vfo_aliases_expire_and_old_generation_cannot_repopulate() -> (
    None
):
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=1,
        max_age=20.0,
        coherence_window=5.0,
    )
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=10.0), generation=1
    )
    store.apply(_observation(FieldPath.active_slot("0"), "A", at=10.1))
    knob = _observation(
        FieldPath.active("0", "freq_mode", "freq_hz"),
        14_130_000,
        at=11.0,
        max_age=5.0,
        source="civ_unsolicited",
    )
    store.apply_relative_vfo_observations((knob,), generation=1)
    alias = FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz")
    assert store.snapshot().field(alias).value == 14_130_000

    store.reset_relative_vfo_retention(generation=2)
    store.discard((FieldPath.active_slot("0"), alias))
    store.apply_relative_vfo_observations(
        (replace(knob, value=14_123_000),), generation=1
    )

    with pytest.raises(KeyError):
        store.snapshot().field(alias)


def test_detached_old_generation_cannot_overwrite_reconnected_vfo_pair() -> None:
    store = StateStore()
    store.configure_relative_vfo_retention(
        generation=1,
        max_age=20.0,
        coherence_window=5.0,
    )
    first = _relative_vfo_observations(at=10.0)
    store.apply_relative_vfo_observations(first, generation=1)
    detached_old_task = replace(first[0], value=14_199_000, timestamp_monotonic=11.0)

    store.reset_relative_vfo_retention(generation=2)
    reconnected = _relative_vfo_observations(
        at=20.0,
        selected_freq=14_181_000,
        unselected_freq=14_076_000,
    )
    store.apply_relative_vfo_observations(reconnected, generation=2)
    store.apply(_observation(FieldPath.active_slot("0"), "A", at=20.7))
    store.apply_relative_vfo_observations(reconnected, generation=2)
    before = store.snapshot()

    changeset = store.apply_relative_vfo_observations(
        (detached_old_task,), generation=1
    )

    after = store.snapshot()
    assert changeset.changes == ()
    assert changeset.observed_paths == ()
    assert after.state_revision == before.state_revision
    assert after.freshness_revision == before.freshness_revision
    assert after.observation_seq == before.observation_seq
    assert after.fields == before.fields
    assert after.field("receiver.0.active.freq_mode.freq_hz").value == 14_181_000
    assert after.field("receiver.0.slot.A.freq_mode.freq_hz").value == 14_181_000


def test_bound_relative_vfo_alias_shares_complete_pair_expiry() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=1,
        max_age=20.0,
        coherence_window=5.0,
    )
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=10.0), generation=1
    )
    store.apply(_observation(FieldPath.active_slot("0"), "A", at=10.1))
    store.apply_relative_vfo_observations(
        (
            _observation(
                FieldPath.active("0", "freq_mode", "freq_hz"),
                14_130_000,
                at=11.0,
                max_age=5.0,
                source="civ_unsolicited",
            ),
        ),
        generation=1,
    )
    alias = FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz")
    relative = FieldPath.active("0", "freq_mode", "freq_hz")
    assert (
        store.snapshot().field(alias).max_age
        == store.snapshot().field(relative).max_age
    )

    clock.advance(20.7)
    store.mark_stale_due()
    snapshot = store.snapshot()
    assert snapshot.field(relative).freshness is FreshnessState.STALE
    assert snapshot.field(alias).freshness is FreshnessState.STALE


@pytest.mark.parametrize("active_slot", ["A", "B"])
def test_bound_relative_vfo_pair_and_all_aliases_expire_at_exact_deadline(
    active_slot: str,
) -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=1,
        max_age=20.0,
        coherence_window=5.0,
    )
    pair = _relative_vfo_observations(at=10.0)
    store.apply_relative_vfo_observations(pair, generation=1)
    store.apply(_observation(FieldPath.active_slot("0"), active_slot, at=10.7))
    store.apply_relative_vfo_observations(
        tuple(replace(item, timestamp_monotonic=10.7) for item in pair),
        generation=1,
    )
    deadline = _relative_vfo_deadline(store)
    tuple_paths = {
        path
        for builder in (FieldPath.active, FieldPath.unselected)
        for path in (
            builder("0", "freq_mode", name)
            for name in ("freq_hz", "mode", "filter_num", "data_mode")
        )
    }
    alias_paths = {
        FieldPath.vfo_slot("0", slot, "freq_mode", name)
        for slot in ("A", "B")
        for name in ("freq_hz", "mode", "filter_num", "data_mode")
    }
    all_value_paths = tuple_paths | alias_paths
    assert all(
        store.snapshot().field(path).freshness is FreshnessState.FRESH
        for path in all_value_paths
    )

    assert store.mark_stale_due(now=deadline - 0.001).freshness == ()
    assert all(
        store.snapshot().field(path).freshness is FreshnessState.FRESH
        for path in all_value_paths
    )

    exact = store.mark_stale_due(now=deadline)
    assert {transition.path for transition in exact.freshness} == all_value_paths
    assert all(
        store.snapshot().field(path).freshness is FreshnessState.STALE
        for path in all_value_paths
    )
    assert (
        store.snapshot().field(FieldPath.active_slot("0")).freshness
        is FreshnessState.FRESH
    )

    assert store.mark_stale_due(now=deadline + 1.0).freshness == ()


def test_explicit_binding_readback_uses_same_retention_and_alias_semantics() -> None:
    clock = FreshnessClock(start=50.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=3,
        max_age=20.0,
        coherence_window=5.0,
    )
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=50.0), generation=3
    )
    store.apply(_observation(FieldPath.active_slot("0"), "A", at=51.0))
    binding_source = SourceMetadata(
        source="command_response",
        provider="vfo_binding",
        transport="fake",
        native_id="explicit_slot_ack_readback",
    )
    binding = tuple(
        replace(item, source=binding_source, timestamp_monotonic=51.0)
        for item in _relative_vfo_observations(
            at=51.0,
            selected_freq=14_164_000,
            unselected_freq=14_076_000,
        )
    )
    for observation in binding:
        store.apply(observation)

    snapshot = store.snapshot()
    for relative_slot, absolute_slot in (
        (VfoSlot.ACTIVE, "A"),
        (VfoSlot.UNSELECTED, "B"),
    ):
        for name in ("freq_hz", "mode", "filter_num", "data_mode"):
            relative = next(
                field
                for field in snapshot.fields
                if field.path.slot is relative_slot and field.path.name == name
            )
            alias = snapshot.field(
                FieldPath.vfo_slot("0", absolute_slot, "freq_mode", name)
            )
            assert alias.value == relative.value
            assert alias.source == relative.source == binding_source
            assert alias.max_age == relative.max_age
            assert alias.max_age is not None


def test_relative_vfo_mandatory_bootstrap_is_atomic_in_every_arrival_order() -> None:
    mandatory = tuple(
        item
        for item in _relative_vfo_observations(at=10.0)
        if item.path.name in {"freq_hz", "mode"}
    )
    for order in permutations(mandatory):
        store = StateStore()
        store.configure_relative_vfo_retention(
            generation=1,
            max_age=20.0,
            coherence_window=5.0,
        )
        for item in order[:-1]:
            store.apply_relative_vfo_observations((item,), generation=1)
            assert store.snapshot().fields == ()
        store.apply_relative_vfo_observations(order[-1:], generation=1)
        assert {field.path for field in store.snapshot().fields} == {
            item.path for item in mandatory
        }


def test_relative_vfo_complete_refresh_extends_one_common_expiry() -> None:
    clock = FreshnessClock(start=902_507.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=3,
        max_age=31.0,
        coherence_window=5.0,
    )
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=902_507.0), generation=3
    )
    first_expiry = _relative_vfo_deadline(store)

    clock.advance(13.13)
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=clock.now()), generation=3
    )
    second_expiry = _relative_vfo_deadline(store)
    assert second_expiry == pytest.approx(first_expiry + 13.13)

    clock.advance(13.13)
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=clock.now()), generation=3
    )
    assert _relative_vfo_deadline(store) == pytest.approx(second_expiry + 13.13)

    clock.advance(5.1)
    assert store.mark_stale_due().freshness == ()
    assert all(
        field.freshness is FreshnessState.FRESH for field in store.snapshot().fields
    )


def test_relative_vfo_expiry_is_atomic_and_lone_late_event_cannot_resurrect() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=4,
        max_age=20.0,
        coherence_window=5.0,
    )
    store.apply_relative_vfo_observations(
        _relative_vfo_observations(at=10.0), generation=4
    )
    expires_at = _relative_vfo_deadline(store)

    clock.advance(expires_at - clock.now())
    delta = store.mark_stale_due()
    assert len(delta.freshness) == 8
    assert {transition.current for transition in delta.freshness} == {
        FreshnessState.STALE
    }
    assert all(
        field.freshness is FreshnessState.STALE for field in store.snapshot().fields
    )

    late_knob = _observation(
        FieldPath.active("0", "freq_mode", "freq_hz"),
        14_286_000,
        at=clock.now() + 0.1,
        max_age=5.0,
        source="civ_unsolicited",
    )
    store.apply_relative_vfo_observations((late_knob,), generation=4)
    assert all(
        field.freshness is FreshnessState.STALE for field in store.snapshot().fields
    )
    assert store.snapshot().field(late_knob.path).value == 14_284_000


def test_relative_vfo_reset_and_old_generation_are_fail_closed() -> None:
    store = StateStore()
    store.configure_relative_vfo_retention(
        generation=1,
        max_age=20.0,
        coherence_window=5.0,
    )
    base = _relative_vfo_observations(at=10.0)
    store.apply_relative_vfo_observations(base, generation=1)

    store.reset_relative_vfo_retention(generation=2)
    assert store.snapshot().fields == ()
    store.apply_relative_vfo_observations(base, generation=1)
    assert store.snapshot().fields == ()

    store.apply_relative_vfo_observations(base[:1], generation=2)
    assert store.snapshot().fields == ()

    store.apply_relative_vfo_observations(base, generation=2)
    store.apply_relative_vfo_observations(
        (replace(base[0], value=None),),
        generation=2,
    )
    assert store.snapshot().fields == ()


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
