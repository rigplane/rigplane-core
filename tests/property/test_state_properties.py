"""Property tests for StateStore snapshot/delta replay and the web DeltaEncoder."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateSnapshot, StateStore
from rigplane.web._delta_encoder import DeltaEncoder, apply_delta

pytestmark = pytest.mark.property

_PATHS = (
    FieldPath.active("0", "freq_mode", "freq_hz"),
    FieldPath.active("0", "freq_mode", "mode"),
    FieldPath.active("1", "freq_mode", "freq_hz"),
    FieldPath.active("1", "freq_mode", "mode"),
)
_SOURCE = SourceMetadata(source="poll_response", provider="property", transport="fake")
# One retained history entry: the second entry appended after a snapshot
# prunes the first, so delta_since must answer requires_full_snapshot for it.
_MAX_HISTORY = 1


def _values(snapshot: StateSnapshot) -> dict[FieldPath, Any]:
    return {field.path: field.value for field in snapshot.fields}


class StateStoreMachine(RuleBasedStateMachine):
    """Drives one StateStore against a dict model of its confirmed values."""

    @initialize()
    def setup(self) -> None:
        self.clock = FreshnessClock(start=0.0)
        self.store = StateStore(
            freshness_clock=self.clock, max_history_count=_MAX_HISTORY
        )
        self.model: dict[FieldPath, int] = {}
        self.bookmarks: list[StateSnapshot] = []
        self.last_revision = 0

    def _observation(self, path: FieldPath, value: int, generation: int) -> Observation:
        return Observation(
            path=path,
            value=value,
            source=_SOURCE,
            timestamp_monotonic=self.clock.advance(0.01),
            provider_generation=generation,
        )

    @rule(path=st.sampled_from(_PATHS), value=st.integers(0, 3))
    def apply_current(self, path: FieldPath, value: int) -> None:
        self.store.apply(self._observation(path, value, self.store.provider_generation))
        self.model[path] = value

    @rule(path=st.sampled_from(_PATHS), value=st.integers(0, 3))
    def apply_from_a_retired_generation(self, path: FieldPath, value: int) -> None:
        if self.store.provider_generation == 0:
            return
        changeset = self.store.apply(
            self._observation(path, value, self.store.provider_generation - 1)
        )
        assert changeset.changes == ()

    @rule(paths=st.lists(st.sampled_from(_PATHS), max_size=3))
    def discard(self, paths: list[FieldPath]) -> None:
        self.store.discard(paths)
        for path in paths:
            self.model.pop(path, None)

    @rule()
    def begin_provider_generation(self) -> None:
        self.store.begin_provider_generation()
        self.model.clear()

    @rule()
    def take_snapshot(self) -> None:
        self.bookmarks.append(self.store.snapshot())

    @invariant()
    def delta_replay_from_every_bookmark_reproduces_the_snapshot(self) -> None:
        current = _values(self.store.snapshot())
        for bookmark in self.bookmarks:
            delta = self.store.delta_since(bookmark)
            if bookmark.provider_generation != self.store.provider_generation:
                assert delta.requires_full_snapshot
            if delta.requires_full_snapshot:
                assert delta.changes == ()
                continue
            replayed = _values(bookmark)
            for change in delta.changes:
                if change.current is None:
                    replayed.pop(change.path, None)
                else:
                    replayed[change.path] = change.current
            assert replayed == current

    @invariant()
    def snapshot_matches_the_model(self) -> None:
        assert _values(self.store.snapshot()) == self.model

    @invariant()
    def state_revision_never_decreases(self) -> None:
        revision = self.store.snapshot().state_revision
        assert revision >= self.last_revision
        self.last_revision = revision


TestStateStoreMachine = StateStoreMachine.TestCase
TestStateStoreMachine.pytestmark = pytest.mark.property


# --- DeltaEncoder / apply_delta ------------------------------------------------

_KEYS = st.sampled_from(("freq", "mode", "ptt", "filter", "split"))
_LEAF = st.one_of(st.none(), st.booleans(), st.integers(-3, 3), st.text(max_size=3))
_STATE = st.dictionaries(
    _KEYS, st.one_of(_LEAF, st.lists(_LEAF, max_size=2)), max_size=5
)


@given(
    st.lists(st.tuples(_STATE, st.booleans()), min_size=1, max_size=12),
    st.integers(1, 4),
)
def test_apply_delta_reconstructs_every_encoded_state(
    states: list[tuple[dict[str, Any], bool]], full_state_interval: int
) -> None:
    encoder = DeltaEncoder(full_state_interval=full_state_interval)
    client: dict[str, Any] = {}
    for state, force_full in states:
        client = apply_delta(client, encoder.encode(state, force_full=force_full))
        assert client == state
