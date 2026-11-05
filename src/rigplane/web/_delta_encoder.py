"""Delta encoding for web state updates — reduce payload for frequent broadcasts.

One diff, two wire renderings (MOR-4206):

- ``DeltaEncoder.encode`` diffs consecutive snapshots down to the leaves and
  returns a leaf delta (``deltaVersion: 2``): ``set`` lists ``[path, value]``
  pairs and ``unset`` lists paths, each path being the list of object keys
  from the state root. The diff descends wherever both the previous and the
  current value are objects; anything else (a scalar, a list, an object that
  replaced a non-object) is one ``set`` of the whole value.
- ``top_level_delta`` renders the same diff as the original top-level delta
  (``changed`` carries each touched top-level value whole, ``removed`` the
  dropped top-level keys) for clients that did not ask for leaf deltas.

Usage::

    encoder = DeltaEncoder()
    encoder.encode({"freq": 14200000, "mode": "USB"})
    # {"type": "full", "data": {"freq": 14200000, "mode": "USB"}, ...}
    encoder.encode({"freq": 14200100, "mode": "USB"})
    # {"type": "delta", "deltaVersion": 2, "set": [[["freq"], 14200100]], ...}
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["DeltaEncoder", "apply_delta", "top_level_delta"]

LEAF_DELTA_VERSION = 2


def _diff(
    prev: dict[str, Any],
    cur: dict[str, Any],
    prefix: list[str],
    set_ops: list[list[Any]],
    unset_ops: list[list[str]],
) -> None:
    """Append the leaf operations that turn ``prev`` into ``cur``."""
    for key, value in cur.items():
        path = [*prefix, key]
        if key not in prev:
            set_ops.append([path, value])
            continue
        old = prev[key]
        if old == value:
            continue
        if isinstance(old, dict) and isinstance(value, dict):
            _diff(old, value, path, set_ops, unset_ops)
        else:
            set_ops.append([path, value])
    for key in prev:
        if key not in cur:
            unset_ops.append([*prefix, key])


class DeltaEncoder:
    """Encode state changes as leaf deltas for efficient WebSocket transmission.

    Tracks previous state and emits only changed leaves.
    Automatically sends full state every N updates to prevent drift.
    """

    def __init__(self, full_state_interval: int = 100) -> None:
        """Initialize delta encoder.

        Args:
            full_state_interval: Number of delta messages before forcing a full state refresh.
                Prevents client/server state drift due to missed messages.
        """
        self._previous_state: dict[str, Any] | None = None
        self._transport_seq: int = 0
        self._delta_count: int = 0
        self._full_state_interval = full_state_interval

    def encode(
        self,
        current_state: dict[str, Any],
        *,
        force_full: bool = False,
        state_revision: int | None = None,
        freshness_revision: int | None = None,
        observation_seq: int | None = None,
    ) -> dict[str, Any]:
        """Encode a state snapshot as a leaf delta or a full state.

        Returns a ``full`` envelope (``data`` is the whole state) on the first
        call, on ``force_full`` and every ``full_state_interval`` deltas;
        otherwise a ``delta`` envelope with ``deltaVersion``, ``set`` and,
        when a key disappeared, ``unset``.
        """
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
            result = {"type": "full", "data": dict(current_state)}
        else:
            set_ops: list[list[Any]] = []
            unset_ops: list[list[str]] = []
            _diff(self._previous_state, current_state, [], set_ops, unset_ops)
            self._delta_count += 1
            result = {
                "type": "delta",
                "deltaVersion": LEAF_DELTA_VERSION,
                "set": set_ops,
            }
            if unset_ops:
                result["unset"] = unset_ops
        self._previous_state = copy.deepcopy(current_state)

        result["revision"] = revision_value
        result["transportSeq"] = self._transport_seq
        if state_revision is not None:
            result["stateRevision"] = revision_value
        if freshness_revision is not None:
            result["freshnessRevision"] = int(freshness_revision)
        if observation_seq is not None:
            result["observationSeq"] = int(observation_seq)
        return result

    @property
    def revision(self) -> int:
        """Current transport sequence counter for legacy callers."""
        return self._transport_seq

    def reset(self) -> None:
        """Reset encoder state (e.g., on client reconnect)."""
        self._previous_state = None
        self._transport_seq = 0
        self._delta_count = 0


def top_level_delta(
    envelope: dict[str, Any], current_state: dict[str, Any]
) -> dict[str, Any]:
    """Render a leaf delta as the top-level delta older clients read.

    ``current_state`` is the snapshot the leaf delta was encoded from. A full
    envelope is returned unchanged.
    """
    if envelope.get("type") != "delta":
        return envelope
    changed: dict[str, Any] = {}
    removed: list[str] = []
    for path, _value in envelope["set"]:
        changed[path[0]] = current_state[path[0]]
    for path in envelope.get("unset", ()):
        if len(path) == 1:
            removed.append(path[0])
        else:
            changed[path[0]] = current_state[path[0]]
    result = {
        key: value
        for key, value in envelope.items()
        if key not in ("deltaVersion", "set", "unset")
    }
    result["changed"] = changed
    if removed:
        result["removed"] = removed
    return result


def _parent_for_write(
    root: dict[str, Any], path: list[str], fresh: set[int]
) -> dict[str, Any]:
    """Walk ``path[:-1]`` copying each object once, so the input stays intact."""
    if not path:
        raise ValueError("Invalid delta path: empty")
    node = root
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            raise ValueError(f"Invalid delta path: {path!r}")
        if id(child) not in fresh:
            child = dict(child)
            node[key] = child
            fresh.add(id(child))
        node = child
    return node


def apply_delta(full_state: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Apply a ``state_update`` envelope (either delta rendering) to a snapshot.

    Returns a new snapshot; ``full_state`` is not modified.

    Raises:
        ValueError: If delta format is invalid.
    """
    if not isinstance(delta, dict):
        raise ValueError(f"Invalid delta format: {type(delta)}")

    delta_type = delta.get("type")

    if delta_type == "full":
        return dict(delta.get("data", {}))

    if delta_type != "delta":
        raise ValueError(f"Unknown delta type: {delta_type}")

    result = dict(full_state)
    if delta.get("deltaVersion") != LEAF_DELTA_VERSION:
        result.update(delta.get("changed", {}))
        for key in delta.get("removed", []):
            result.pop(key, None)
        return result

    fresh = {id(result)}
    for path, value in delta.get("set", []):
        _parent_for_write(result, path, fresh)[path[-1]] = value
    for path in delta.get("unset", []):
        _parent_for_write(result, path, fresh).pop(path[-1], None)
    return result
