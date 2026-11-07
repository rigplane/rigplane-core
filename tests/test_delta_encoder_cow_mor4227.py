"""MOR-4227 equality pins: the delta encoder's copy-on-write snapshot.

``DeltaEncoder.encode`` used to rebuild its whole previous-state snapshot
with ``copy_state_tree`` on every broadcast. It now copies only the changed
branches and reuses the unchanged private subtrees. These tests pin that
the wire output (full and delta envelopes, set/unset ops) is IDENTICAL to
the old full-copy implementation — reproduced here verbatim — and that the
COW snapshot stays isolated from caller mutations of the encoded tree.
"""

from __future__ import annotations

import copy
from random import Random
from typing import Any

from rigplane.web._delta_encoder import DeltaEncoder, copy_state_tree


class _FullCopyEncoder(DeltaEncoder):
    """The pre-MOR-4227 encode(): full copy_state_tree per snapshot."""

    def encode(  # noqa: D102 - see DeltaEncoder.encode
        self,
        current_state: dict[str, Any],
        *,
        force_full: bool = False,
        state_revision: int | None = None,
        freshness_revision: int | None = None,
        observation_seq: int | None = None,
    ) -> Any:
        self._transport_seq += 1
        revision_value = (
            self._transport_seq if state_revision is None else int(state_revision)
        )

        result: dict[str, Any]
        if (
            force_full
            or self._previous_state is None
            or self._delta_count >= self._full_state_interval
        ):
            self._delta_count = 0
            result = {
                "type": "full",
                "revision": revision_value,
                "transportSeq": self._transport_seq,
                "data": dict(current_state),
            }
        else:
            from rigplane.web._delta_encoder import _diff

            set_ops: list[tuple[list[str], Any]] = []
            unset_ops: list[list[str]] = []
            _diff(self._previous_state, current_state, [], set_ops, unset_ops)
            self._delta_count += 1
            result = {
                "type": "delta",
                "revision": revision_value,
                "transportSeq": self._transport_seq,
                "deltaVersion": 2,
                "set": set_ops,
            }
            if unset_ops:
                result["unset"] = unset_ops
        self._previous_state = copy_state_tree(current_state)

        if state_revision is not None:
            result["stateRevision"] = revision_value
        if freshness_revision is not None:
            result["freshnessRevision"] = int(freshness_revision)
        if observation_seq is not None:
            result["observationSeq"] = int(observation_seq)
        return result


def _random_state(rng: Random) -> dict[str, Any]:
    return {
        "freq": rng.randint(14000000, 14350000),
        "mode": rng.choice(("USB", "LSB", "FM", "CW")),
        "s_meter": rng.randint(1, 60),
        "main": {
            "vfo_a": {"freq_hz": rng.randint(1, 99999999), "mode": "USB"},
            "vfo_b": {"freq_hz": rng.randint(1, 99999999), "mode": "LSB"},
            "tags": [rng.randint(0, 9) for _ in range(rng.randint(0, 5))],
        },
        "meters": {f"m{i}": rng.random() for i in range(8)},
        "absent_sometimes": rng.choice((None, {"x": 1})),
    }


def _mutate(state: dict[str, Any], rng: Random) -> dict[str, Any]:
    """Deep-mutate the SAME tree objects in place, like a caller that
    reuses its containers between broadcasts."""
    what = rng.randint(0, 5)
    if what == 0:
        state["freq"] = rng.randint(14000000, 14350000)
    elif what == 1:
        state["main"]["vfo_a"]["freq_hz"] = rng.randint(1, 99999999)
    elif what == 2:
        state["meters"][f"m{rng.randint(0, 7)}"] = rng.random()
    elif what == 3 and "absent_sometimes" in state:
        if rng.random() < 0.5:
            state.pop("absent_sometimes")
        else:
            state["absent_sometimes"] = {"x": rng.randint(0, 9)}
    elif what == 4 and isinstance(state["main"]["tags"], list):
        state["main"]["tags"].append(rng.randint(0, 9))
    else:
        state["main"]["vfo_b"]["mode"] = rng.choice(("USB", "FM"))
    return state


def test_cow_frames_identical_to_full_copy_implementation() -> None:
    """Every frame of a randomized mutate/refresh sequence — full envelope,
    delta set/unset ops, revisions — matches the full-copy encoder."""
    for seed in range(8):
        rng = Random(seed)
        cow = DeltaEncoder(full_state_interval=7)
        full = _FullCopyEncoder(full_state_interval=7)
        state = _random_state(rng)
        for step in range(40):
            action = rng.random()
            if action < 0.3:
                state = _random_state(rng)  # fresh tree, like a fresh build
            else:
                _mutate(state, rng)
            revision = 1000 + step
            fresh = rng.random() < 0.1
            expected = full.encode(
                copy.deepcopy(state),
                force_full=fresh,
                state_revision=revision,
                freshness_revision=revision * 2,
                observation_seq=step,
            )
            actual = cow.encode(
                copy.deepcopy(state),
                force_full=fresh,
                state_revision=revision,
                freshness_revision=revision * 2,
                observation_seq=step,
            )
            assert actual == expected, (seed, step)


def test_cow_snapshot_isolated_from_post_encode_mutation() -> None:
    """Mutating the encoded tree (changed AND unchanged branches, lists)
    must not leak into the encoder's snapshot: a later encode against the
    ORIGINAL values differs in exactly the one leaf the later body really
    changed — no spurious ops reverting caller mutations."""
    encoder = DeltaEncoder()
    body = {
        "a": {"x": 1, "deep": {"y": [1, 2]}},
        "b": {"keep": "me"},
        "tags": [1, 2, 3],
        "scalar": 5,
    }
    encoder.encode(body)
    # Caller reuses and mutates everything it can reach.
    body["a"]["x"] = 999
    body["a"]["deep"]["y"].append(3)
    body["b"]["keep"] = "changed"
    body["tags"].append(4)

    original = {
        "a": {"x": 1, "deep": {"y": [1, 2]}},
        "b": {"keep": "me"},
        "tags": [1, 2, 3],
        "scalar": 5,
    }
    original["a"]["x"] = 2  # the one real change this broadcast carries
    frame = encoder.encode(original)
    assert frame["type"] == "delta"
    assert frame["set"] == [(["a", "x"], 2)], (
        "caller mutations must not leak into the stored snapshot"
    )


def test_cow_reuses_unchanged_subtrees_and_copies_changed_branches() -> None:
    """The performance intent, pinned structurally: an unchanged subtree is
    the SAME object across encodes; a changed branch is a private copy."""
    encoder = DeltaEncoder()
    encoder.encode({"keep": {"deep": 1}, "change": "old", "same_list": [1, 2]})
    first = encoder._previous_state
    assert first["keep"] == {"deep": 1}

    encoder.encode({"keep": {"deep": 1}, "change": "new", "same_list": [1, 2]})
    second = encoder._previous_state

    assert second["keep"] is first["keep"], "unchanged subtree must be reused"
    assert second["same_list"] is first["same_list"]
    assert second["change"] == "new"
    assert second is not first


def test_cow_same_tree_object_passed_twice_stays_isolated() -> None:
    """A caller encoding the very same tree object twice must not alias the
    snapshot onto the caller's containers."""
    encoder = DeltaEncoder()
    body: dict[str, Any] = {"nested": {"list": [1]}}
    encoder.encode(body)
    body["nested"]["list"].append(2)
    frame = encoder.encode(body)  # same object, now mutated
    assert frame["set"] == [(["nested", "list"], [1, 2])]

    body["nested"]["list"].append(3)  # the caller keeps mutating its tree
    frame = encoder.encode({"nested": {"list": [1, 2]}})
    assert frame["set"] == [], "the stored snapshot must not alias the caller"
