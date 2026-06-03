"""Tests for delta encoding of web state updates."""

from __future__ import annotations

import pytest

from rigplane.web._delta_encoder import DeltaEncoder, apply_delta


class TestDeltaEncoder:
    """Test delta encoder for incremental state updates."""

    def test_initial_state_is_full(self):
        """First encode should return full state."""
        encoder = DeltaEncoder()
        state = {"freq": 14200000, "mode": "USB", "power": 100}

        delta = encoder.encode(state)

        assert delta["type"] == "full"
        assert delta["data"] == state
        assert delta["revision"] == 1

    def test_unchanged_state_sends_delta(self):
        """Encoding identical state should send empty delta."""
        encoder = DeltaEncoder()
        state = {"freq": 14200000, "mode": "USB"}

        encoder.encode(state)
        delta = encoder.encode(state)

        assert delta["type"] == "delta"
        assert delta["changed"] == {}
        assert "removed" not in delta
        assert delta["revision"] == 2

    def test_changed_field_in_delta(self):
        """Changed fields should appear in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100}
        state2 = {"freq": 14200100, "mode": "USB", "power": 100}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["changed"] == {"freq": 14200100}
        assert "removed" not in delta

    def test_multiple_changed_fields(self):
        """Multiple field changes should all appear in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100}
        state2 = {"freq": 14200100, "mode": "CW", "power": 75}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["changed"] == {"freq": 14200100, "mode": "CW", "power": 75}

    def test_new_field_in_delta(self):
        """New fields should appear in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB"}
        state2 = {"freq": 14200000, "mode": "USB", "power": 100}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["changed"] == {"power": 100}

    def test_removed_field_in_delta(self):
        """Removed fields should appear in 'removed' list."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100}
        state2 = {"freq": 14200000, "mode": "USB"}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["removed"] == ["power"]

    def test_multiple_changes_and_removals(self):
        """Complex state changes should be captured in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100, "agc": "FAST"}
        state2 = {"freq": 14200100, "mode": "CW", "agc": "SLOW"}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["changed"] == {"freq": 14200100, "mode": "CW", "agc": "SLOW"}
        assert delta["removed"] == ["power"]

    def test_force_full_state(self):
        """force_full=True should send full state."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB"}
        state2 = {"freq": 14200000, "mode": "USB"}

        encoder.encode(state1)
        delta = encoder.encode(state2, force_full=True)

        assert delta["type"] == "full"
        assert delta["data"] == state2
        assert delta["revision"] == 2

    def test_periodic_full_state_refresh(self):
        """Should send full state every N delta updates."""
        encoder = DeltaEncoder(full_state_interval=2)
        state = {"freq": 14200000, "mode": "USB"}

        # First message: full
        delta1 = encoder.encode(state)
        assert delta1["type"] == "full"
        assert delta1["revision"] == 1

        # Next 2 messages: deltas
        delta2 = encoder.encode(state)
        assert delta2["type"] == "delta"
        assert delta2["revision"] == 2

        delta3 = encoder.encode(state)
        assert delta3["type"] == "delta"
        assert delta3["revision"] == 3

        # 4th message: full refresh (because delta_count >= interval)
        delta4 = encoder.encode(state)
        assert delta4["type"] == "full"
        assert delta4["revision"] == 4

    def test_revision_counter(self):
        """Revision should increment on each encode."""
        encoder = DeltaEncoder()
        state = {"freq": 14200000}

        delta1 = encoder.encode(state)
        assert delta1["revision"] == 1

        delta2 = encoder.encode(state)
        assert delta2["revision"] == 2

        delta3 = encoder.encode(state)
        assert delta3["revision"] == 3

    def test_transport_sequence_is_split_from_canonical_state_revision(self):
        """Canonical state revision must not be backed by the transport counter."""
        encoder = DeltaEncoder()
        state = {"freq": 14200000, "stateRevision": 10, "revision": 10}

        full = encoder.encode(state, state_revision=10, freshness_revision=1)
        delta = encoder.encode(state, state_revision=10, freshness_revision=1)

        assert full["revision"] == 10
        assert full["stateRevision"] == 10
        assert full["freshnessRevision"] == 1
        assert full["transportSeq"] == 1
        assert delta["revision"] == 10
        assert delta["stateRevision"] == 10
        assert delta["freshnessRevision"] == 1
        assert delta["transportSeq"] == 2

    def test_reset_encoder(self):
        """reset() should clear state tracking."""
        encoder = DeltaEncoder()
        state = {"freq": 14200000}

        encoder.encode(state)
        encoder.reset()

        # Next encode should be full state again
        delta = encoder.encode(state)
        assert delta["type"] == "full"
        assert delta["revision"] == 1

    def test_nested_dict_change(self):
        """Changes in nested dicts should be detected."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "radio": {"model": "IC-7610", "version": 1}}
        state2 = {"freq": 14200000, "radio": {"model": "IC-7610", "version": 2}}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        # Entire nested dict is sent if changed
        assert delta["changed"] == {"radio": {"model": "IC-7610", "version": 2}}

    def test_empty_state(self):
        """Empty states should be handled."""
        encoder = DeltaEncoder()

        delta1 = encoder.encode({})
        assert delta1["type"] == "full"
        assert delta1["data"] == {}

        delta2 = encoder.encode({})
        assert delta2["type"] == "delta"
        assert delta2["changed"] == {}

    def test_large_state(self):
        """Large states should encode efficiently."""
        encoder = DeltaEncoder()
        state1 = {f"field_{i}": i for i in range(100)}
        state2 = {f"field_{i}": i for i in range(100)}
        state2["field_50"] = 500  # Change one field

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        # Only the changed field should be in delta
        assert delta["changed"] == {"field_50": 500}
        assert len(delta["changed"]) == 1


class TestApplyDelta:
    """Test delta application on client side."""

    def test_apply_full_state(self):
        """Applying full state should replace entire state."""
        full_state = {"freq": 14200000, "mode": "USB"}
        delta = {
            "type": "full",
            "data": {"freq": 14200100, "mode": "CW", "power": 100},
        }

        result = apply_delta(full_state, delta)

        assert result == {"freq": 14200100, "mode": "CW", "power": 100}

    def test_apply_delta_changes(self):
        """Applying delta should update only changed fields."""
        full_state = {"freq": 14200000, "mode": "USB", "power": 100}
        delta = {"type": "delta", "changed": {"freq": 14200100}}

        result = apply_delta(full_state, delta)

        assert result == {"freq": 14200100, "mode": "USB", "power": 100}

    def test_apply_delta_with_removals(self):
        """Applying delta should remove specified fields."""
        full_state = {"freq": 14200000, "mode": "USB", "power": 100}
        delta = {"type": "delta", "changed": {}, "removed": ["power"]}

        result = apply_delta(full_state, delta)

        assert result == {"freq": 14200000, "mode": "USB"}

    def test_apply_delta_with_changes_and_removals(self):
        """Applying delta should handle both changes and removals."""
        full_state = {"freq": 14200000, "mode": "USB", "power": 100, "agc": "FAST"}
        delta = {
            "type": "delta",
            "changed": {"freq": 14200100},
            "removed": ["power"],
        }

        result = apply_delta(full_state, delta)

        assert result == {"freq": 14200100, "mode": "USB", "agc": "FAST"}

    def test_apply_delta_adds_new_field(self):
        """Applying delta should add new fields."""
        full_state = {"freq": 14200000, "mode": "USB"}
        delta = {"type": "delta", "changed": {"power": 100}}

        result = apply_delta(full_state, delta)

        assert result == {"freq": 14200000, "mode": "USB", "power": 100}

    def test_invalid_delta_type(self):
        """Invalid delta type should raise error."""
        full_state = {"freq": 14200000}
        delta = {"type": "invalid"}

        with pytest.raises(ValueError, match="Unknown delta type"):
            apply_delta(full_state, delta)

    def test_invalid_delta_format(self):
        """Invalid delta format should raise error."""
        full_state = {"freq": 14200000}

        with pytest.raises(ValueError, match="Invalid delta format"):
            apply_delta(full_state, "not a dict")

    def test_roundtrip_encoding_and_application(self):
        """Encoding and applying deltas should preserve state."""
        encoder = DeltaEncoder()
        original = {"freq": 14200000, "mode": "USB", "power": 100}

        # Encode original state
        delta1 = encoder.encode(original)
        state = apply_delta({}, delta1)
        assert state == original

        # Make a change
        modified = {"freq": 14200100, "mode": "USB", "power": 100}
        delta2 = encoder.encode(modified)
        state = apply_delta(state, delta2)
        assert state == modified

        # Remove a field
        reduced = {"freq": 14200100, "mode": "USB"}
        delta3 = encoder.encode(reduced)
        state = apply_delta(state, delta3)
        assert state == reduced
