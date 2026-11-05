"""StateStore snapshots are rebuilt per changed field, not per store (MOR-4212).

Every state broadcast took a full snapshot (a deep copy of every field) and
rebuilt the whole public state. The store now keeps each field's projection
until that field changes, so a one-field change re-projects one field. These
tests pin that, and check after every step of random store histories that the
incremental snapshot and the public state built from it equal a full rebuild.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from typing import Any

import pytest

from rigplane.core import state_store as state_store_module
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.core.state_store import (
    FreshnessClock,
    StateSnapshot,
    StateStore,
)
from rigplane.web import runtime_helpers
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot

_FREQ = FieldPath.receiver("0", "freq_mode", "freq_hz")
_SMETER = FieldPath.receiver("0", "meters", "s_meter")
_PTT = FieldPath.global_("tx_state", "ptt")
_TX_TARGET = FieldPath.global_("tx_state", "tx_target")
_FIXED_EDGE = FieldPath.scope_control("display", "fixed_edge")

# Paths across every public-state section, including the relative-VFO
# retention paths (which mutate max_age in place) and dict-valued fields.
_PATHS = (
    _FREQ,
    _SMETER,
    _PTT,
    _TX_TARGET,
    _FIXED_EDGE,
    FieldPath.receiver("0", "freq_mode", "mode"),
    FieldPath.receiver("main", "freq_mode", "freq_hz"),
    FieldPath.receiver("1", "freq_mode", "freq_hz"),
    FieldPath.active("0", "freq_mode", "freq_hz"),
    FieldPath.active("0", "freq_mode", "mode"),
    FieldPath.unselected("0", "freq_mode", "freq_hz"),
    FieldPath.unselected("0", "freq_mode", "mode"),
    FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz"),
    FieldPath.vfo_slot("0", "B", "freq_mode", "mode"),
    FieldPath.active_slot("0"),
    FieldPath.receiver("0", "operator_controls", "af_level"),
    FieldPath.receiver("0", "operator_toggles", "dcd"),
    FieldPath.global_("operator_controls", "power_level"),
    FieldPath.global_("meters", "swr"),
    FieldPath.global_("slow_state", "active"),
    FieldPath.scope_control("display", "span"),
    FieldPath.parse("connection.connection.connected"),
)
_SCALAR_PATHS = tuple(path for path in _PATHS if path not in (_TX_TARGET, _FIXED_EDGE))


def _source(name: str = "poll_response") -> SourceMetadata:
    return SourceMetadata(source=name, provider="test", transport="fake")


def _observation(
    path: FieldPath, value: Any, *, at: float, max_age: float | None = None
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_source(),
        timestamp_monotonic=at,
        max_age=max_age,
    )


def _full_rebuild(store: StateStore, snapshot: StateSnapshot) -> StateSnapshot:
    """Project every entry from scratch, the way snapshot() did before."""

    return replace(
        snapshot,
        fields=tuple(
            state_store_module._field_snapshot(path, entry)
            for path, entry in sorted(
                store._entries.items(), key=lambda item: str(item[0])
            )
        ),
    )


def _public(snapshot: StateSnapshot) -> dict[str, Any]:
    return build_public_state_payload_from_snapshot(
        snapshot,
        radio=None,
        receiver_count=2,
        availability={_SMETER: False},
        declared=frozenset(_PATHS) - {FieldPath.global_("meters", "swr")},
        updated_at="2026-11-05T00:00:00+00:00",
        monitor_mute={"on": False},
    )


def _clear_public_state_caches() -> None:
    runtime_helpers._to_camel.cache_clear()
    runtime_helpers._field_status_seeds.cache_clear()


def test_one_field_change_reprojects_only_that_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(freshness_clock=FreshnessClock(start=100.0))
    for index, path in enumerate(_SCALAR_PATHS[:12]):
        store.apply(_observation(path, index, at=100.0 + index))
    before = store.snapshot()

    projected: list[FieldPath] = []
    real = state_store_module._field_snapshot

    def counting(path: FieldPath, entry: Any) -> Any:
        projected.append(path)
        return real(path, entry)

    monkeypatch.setattr(state_store_module, "_field_snapshot", counting)
    store.apply(_observation(_SMETER, 99, at=200.0))
    after = store.snapshot()

    assert projected == [_SMETER]
    assert after.field(_SMETER).value == 99
    unchanged = [field for field in before.fields if field.path != _SMETER]
    assert all(after.field(field.path) is field for field in unchanged)

    projected.clear()
    again = store.snapshot()
    assert projected == []
    assert again.fields is after.fields


def test_a_stale_mark_reprojects_only_the_aged_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(freshness_clock=FreshnessClock(start=100.0))
    store.apply(_observation(_FREQ, 14_074_000, at=100.0))
    store.apply(_observation(_SMETER, 3, at=100.0, max_age=1.0))
    store.snapshot()

    projected: list[FieldPath] = []
    real = state_store_module._field_snapshot
    monkeypatch.setattr(
        state_store_module,
        "_field_snapshot",
        lambda path, entry: projected.append(path) or real(path, entry),
    )
    store.mark_stale_due(now=105.0)

    assert store.snapshot().field(_SMETER).freshness.value == "stale"
    assert projected == [_SMETER]


def test_mutable_values_are_still_copied_per_snapshot() -> None:
    store = StateStore()
    edge = {"range_index": 1, "edge": 2, "start_hz": 14_000_000, "end_hz": 14_350_000}
    store.apply(_observation(_FIXED_EDGE, dict(edge), at=1.0))
    store.apply(_observation(_PTT, False, at=1.0))

    first = store.snapshot()
    first.field(_FIXED_EDGE).value["edge"] = 3

    assert store.snapshot().field(_FIXED_EDGE).value == edge
    assert store.field(_FIXED_EDGE).value == edge
    assert store.snapshot().field(_PTT) is first.field(_PTT)


def test_frozen_dataclass_values_are_shared() -> None:
    store = StateStore()
    store.apply(
        _observation(
            _TX_TARGET,
            KnownTxTarget(receiver="MAIN", slot="A", frequency_hz=14_074_000),
            at=1.0,
        )
    )

    assert store.snapshot().field(_TX_TARGET) is store.snapshot().field(_TX_TARGET)


def test_snapshot_field_lookup_matches_a_scan_also_after_replace() -> None:
    store = StateStore()
    for index, path in enumerate(_SCALAR_PATHS[:6]):
        store.apply(_observation(path, index, at=float(index)))
    snapshot = store.snapshot()

    for field in snapshot.fields:
        assert snapshot.field(field.path) is field
        assert snapshot.field(str(field.path)) is field
    narrowed = replace(snapshot, fields=snapshot.fields[:2])
    with pytest.raises(KeyError):
        narrowed.field(snapshot.fields[3].path)
    assert narrowed.field(snapshot.fields[1].path) is snapshot.fields[1]


def _value(rng: random.Random, path: FieldPath) -> Any:
    if path == _TX_TARGET:
        return rng.choice(
            [
                KnownTxTarget(
                    receiver="MAIN", slot=rng.choice("AB"), frequency_hz=7_074_000
                ),
                UnknownTxTarget(reason="not-observed"),
            ]
        )
    if path == _FIXED_EDGE:
        return {"range_index": rng.randint(0, 3), "edge": 1, "start_hz": 1, "end_hz": 2}
    if path.name == "mode":
        return rng.choice(["USB", "LSB", "CW", "FM_N", None])
    if path.name == "active_slot":
        return rng.choice(["A", "B"])
    return rng.choice(
        [rng.randint(0, 30_000_000), rng.random(), True, False, None, (1, 2)]
    )


@pytest.mark.parametrize("seed", range(40))
def test_incremental_state_equals_full_rebuild_after_every_step(seed: int) -> None:
    rng = random.Random(seed)
    clock = FreshnessClock(start=1_000.0)
    store = StateStore(freshness_clock=clock)
    if rng.random() < 0.5:
        store.configure_relative_vfo_retention(
            generation=0, max_age=5.0, coherence_window=2.0
        )
    for _step in range(60):
        roll = rng.random()
        clock.advance(rng.choice([0.0, 0.05, 0.4, 2.0]))
        if roll < 0.70:
            path = rng.choice(_PATHS)
            store.apply_current(
                _observation(
                    path,
                    _value(rng, path),
                    at=clock.now() - rng.choice([0.0, 0.0, 0.3]),
                    max_age=rng.choice([None, 1.0, 3.0]),
                )
            )
        elif roll < 0.82:
            store.mark_stale_due()
        elif roll < 0.94:
            store.discard(rng.sample(_PATHS, rng.randint(1, 3)))
        elif roll < 0.97:
            store.begin_provider_generation()
        else:
            store.snapshot()

        incremental = store.snapshot()
        reference = _full_rebuild(store, incremental)
        assert incremental == reference
        for field in reference.fields:
            assert incremental.field(field.path) == field

        public = _public(incremental)
        _clear_public_state_caches()
        rebuilt = _public(reference)
        assert json.dumps(public, sort_keys=False) == json.dumps(
            rebuilt, sort_keys=False
        )
