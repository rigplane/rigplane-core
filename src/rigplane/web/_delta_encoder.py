"""Delta encoding for web state updates — reduce payload for frequent broadcasts.

This module provides efficient differential state encoding for WebSocket clients:
- Compute deltas between consecutive state snapshots
- Send only changed fields instead of full state
- Clients apply deltas to reconstruct full state
- Periodic full state refresh prevents drift

Usage::

    encoder = DeltaEncoder()

    # First update: send full state
    delta = encoder.encode({"freq": 14200000, "mode": "USB"})
    # delta = {"type": "full", "data": {"freq": 14200000, "mode": "USB"}}

    # Subsequent updates: send only changes
    delta = encoder.encode({"freq": 14200100, "mode": "USB"})
    # delta = {"type": "delta", "changed": {"freq": 14200100}, "revision": 1}

    # Periodic full refresh
    delta = encoder.encode({"freq": 14200200, "mode": "CW"}, force_full=True)
    # delta = {"type": "full", "data": {...}, "revision": 2}
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["DeltaEncoder", "apply_delta"]


class DeltaEncoder:
    """Encode state changes as deltas for efficient WebSocket transmission.

    Tracks previous state and emits only changed fields.
    Automatically sends full state every N updates to prevent drift.
    """

    def __init__(self, full_state_interval: int = 100) -> None:
        """Initialize delta encoder.

        Args:
            full_state_interval: Number of delta messages before forcing a full state refresh.
                Prevents client/server state drift due to missed messages.
        """
        self._previous_state: dict[str, Any] | None = None
        self._revision: int = 0
        self._delta_count: int = 0
        self._full_state_interval = full_state_interval

    def encode(
        self, current_state: dict[str, Any], *, force_full: bool = False
    ) -> dict[str, Any]:
        """Encode a state snapshot as a delta or full state.

        Args:
            current_state: Current state snapshot (typically the public state payload).
            force_full: If True, always send full state (for initial handshake or recovery).

        Returns:
            Delta message in one of these formats:

            Full state (first message or periodic refresh)::

                {
                    "type": "full",
                    "data": {...current_state...},
                    "revision": 0
                }

            Delta message (most updates)::

                {
                    "type": "delta",
                    "changed": {...changed_fields...},
                    "removed": [...deleted_keys...],
                    "revision": 1
                }
        """
        # Check if full state refresh is needed
        if (
            force_full
            or self._previous_state is None
            or self._delta_count >= self._full_state_interval
        ):
            # Send full state
            self._previous_state = copy.deepcopy(current_state)
            self._revision += 1
            self._delta_count = 0
            return {
                "type": "full",
                "data": dict(current_state),
                "revision": self._revision,
            }

        # Compute delta
        changed: dict[str, Any] = {}
        removed: list[str] = []

        # Find changed and new fields
        for key, value in current_state.items():
            prev_value = self._previous_state.get(key, _MISSING)
            if prev_value is _MISSING or value != prev_value:
                changed[key] = value

        # Find removed fields
        for key in self._previous_state:
            if key not in current_state:
                removed.append(key)

        # Update previous state for next comparison
        self._previous_state = copy.deepcopy(current_state)
        self._revision += 1
        self._delta_count += 1

        # Build delta message
        result: dict[str, Any] = {
            "type": "delta",
            "changed": changed,
            "revision": self._revision,
        }
        if removed:
            result["removed"] = removed

        return result

    @property
    def revision(self) -> int:
        """Current revision counter."""
        return self._revision

    def reset(self) -> None:
        """Reset encoder state (e.g., on client reconnect)."""
        self._previous_state = None
        self._revision = 0
        self._delta_count = 0


class _Missing:
    """Sentinel for missing dictionary values."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING = _Missing()


def apply_delta(full_state: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Apply a delta message to a full state snapshot.

    Used by web clients to reconstruct state from deltas.

    Args:
        full_state: Previous full state snapshot.
        delta: Delta message from DeltaEncoder.

    Returns:
        Updated state snapshot with delta applied.

    Raises:
        ValueError: If delta format is invalid.
    """
    if not isinstance(delta, dict):
        raise ValueError(f"Invalid delta format: {type(delta)}")

    delta_type = delta.get("type")

    if delta_type == "full":
        # Replace entire state
        return dict(delta.get("data", {}))

    if delta_type == "delta":
        # Apply incremental changes
        result = dict(full_state)
        changed = delta.get("changed", {})
        removed = delta.get("removed", [])

        result.update(changed)
        for key in removed:
            result.pop(key, None)

        return result

    raise ValueError(f"Unknown delta type: {delta_type}")
