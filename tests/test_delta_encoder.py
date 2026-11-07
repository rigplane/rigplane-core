"""Tests for delta encoding of web state updates."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import aiohttp
import pydantic
import pytest

from rigplane._bounded_queue import BoundedQueue
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.web._delta_encoder import (
    DeltaEncoder,
    StateUpdateFrame,
    apply_delta,
    copy_state_tree,
    top_level_delta,
)
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.state_schema import StateUpdateEnvelope
from scripts.soak import soak_driver


def _freq_observation(at: float) -> Observation:
    return Observation(
        path=FieldPath.active("0", "freq_mode", "freq_hz"),
        value=14_074_000,
        source=SourceMetadata(
            source="poll_response",
            provider="test",
            transport="fake",
            native_id="test",
        ),
        timestamp_monotonic=at,
        max_age=None,
    )


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
        assert delta["deltaVersion"] == 2
        assert delta["set"] == []
        assert "unset" not in delta
        assert delta["revision"] == 2

    def test_changed_field_in_delta(self):
        """Changed fields should appear in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100}
        state2 = {"freq": 14200100, "mode": "USB", "power": 100}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["set"] == [(["freq"], 14200100)]
        assert "unset" not in delta

    def test_multiple_changed_fields(self):
        """Multiple field changes should all appear in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100}
        state2 = {"freq": 14200100, "mode": "CW", "power": 75}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["set"] == [
            (["freq"], 14200100),
            (["mode"], "CW"),
            (["power"], 75),
        ]

    def test_new_field_in_delta(self):
        """New fields should appear in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB"}
        state2 = {"freq": 14200000, "mode": "USB", "power": 100}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["set"] == [(["power"], 100)]

    def test_first_observation_delta_carries_the_real_value_from_null(self):
        """MOR-2513: an unobserved leaf publishes null, and the delta for
        its first observation carries the real value (null -> value)."""
        empty = build_public_state_payload_from_snapshot(
            StateStore().snapshot(), radio=None, receiver_count=1
        )
        assert empty["main"]["freqHz"] is None

        encoder = DeltaEncoder()
        full = encoder.encode(empty)
        assert full["type"] == "full"
        assert full["data"]["main"]["freqHz"] is None

        store = StateStore()
        store.apply(_freq_observation(1.0))
        observed = build_public_state_payload_from_snapshot(
            store.snapshot(), radio=None, receiver_count=1
        )
        delta = encoder.encode(observed)

        assert delta["type"] == "delta"
        assert (["main", "freqHz"], 14_074_000) in delta["set"]

    def test_removed_field_in_delta(self):
        """Removed fields should appear in 'removed' list."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100}
        state2 = {"freq": 14200000, "mode": "USB"}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["unset"] == [["power"]]

    def test_multiple_changes_and_removals(self):
        """Complex state changes should be captured in delta."""
        encoder = DeltaEncoder()
        state1 = {"freq": 14200000, "mode": "USB", "power": 100, "agc": "FAST"}
        state2 = {"freq": 14200100, "mode": "CW", "agc": "SLOW"}

        encoder.encode(state1)
        delta = encoder.encode(state2)

        assert delta["type"] == "delta"
        assert delta["set"] == [
            (["freq"], 14200100),
            (["mode"], "CW"),
            (["agc"], "SLOW"),
        ]
        assert delta["unset"] == [["power"]]

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

    def test_observation_sequence_is_carried_without_changing_semantic_revision(self):
        """Observation metadata sequence is distinct from state revision."""
        encoder = DeltaEncoder()
        state = {
            "freq": 14200000,
            "stateRevision": 10,
            "revision": 10,
            "observationSeq": 3,
        }

        full = encoder.encode(
            state,
            state_revision=10,
            freshness_revision=1,
            observation_seq=3,
        )
        delta = encoder.encode(
            {**state, "observationSeq": 4},
            state_revision=10,
            freshness_revision=1,
            observation_seq=4,
        )

        assert full["revision"] == 10
        assert full["stateRevision"] == 10
        assert full["observationSeq"] == 3
        assert delta["revision"] == 10
        assert delta["stateRevision"] == 10
        assert delta["observationSeq"] == 4

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
        # Only the changed leaf is sent (MOR-4206).
        assert delta["set"] == [(["radio", "version"], 2)]

    def test_empty_state(self):
        """Empty states should be handled."""
        encoder = DeltaEncoder()

        delta1 = encoder.encode({})
        assert delta1["type"] == "full"
        assert delta1["data"] == {}

        delta2 = encoder.encode({})
        assert delta2["type"] == "delta"
        assert delta2["set"] == []

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
        assert delta["set"] == [(["field_50"], 500)]


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


def _status_entry(index: int, observed_at: float) -> dict[str, Any]:
    """A field-status row shaped like the wire's (about 400 bytes of JSON)."""
    return {
        "storePath": f"receiver.0.levels.field_number_{index}",
        "observed": True,
        "freshness": "fresh",
        "availability": "available",
        "lastObservedMonotonic": observed_at,
        "maxAge": 1.5,
        "source": SourceMetadata(
            source="poll_response",
            provider="icom",
            transport="lan",
            native_id=f"0x14 0x{index:02x}",
        ).to_dict(),
        "quality": ["direct"],
    }


def _station_state(observed_at: dict[int, float]) -> dict[str, Any]:
    return {
        "main": {"freqHz": 14_074_000, "mode": "USB"},
        "connection": {"rigConnected": True, "radioReady": True},
        "fieldStatus": {
            f"main.field{index}": _status_entry(index, at)
            for index, at in observed_at.items()
        },
    }


class TestLeafDelta:
    """MOR-4206: a delta carries the changed leaves, not whole top-level maps."""

    def test_source_metadata_change_is_a_native_id_leaf(self):
        """A re-read through another command moves only ``source.nativeId``."""

        def public_state(native_id: str) -> dict[str, Any]:
            store = StateStore()
            store.apply(
                Observation(
                    path=FieldPath.active("0", "freq_mode", "freq_hz"),
                    value=14_074_000,
                    source=SourceMetadata(
                        source="poll_response",
                        provider="test",
                        transport="fake",
                        native_id=native_id,
                    ),
                    timestamp_monotonic=1.0,
                    max_age=None,
                )
            )
            return build_public_state_payload_from_snapshot(
                store.snapshot(), radio=None, receiver_count=1
            )

        before = public_state("0x03")
        after = public_state("0x25 0x00")
        encoder = DeltaEncoder()
        encoder.encode(before)

        delta = encoder.encode(after)

        # ``updatedAt`` follows the wall clock; every other op is the source's.
        assert [op for op in delta["set"] if op[0] != ["updatedAt"]] == [
            (["fieldStatus", "main.freqHz", "source", "nativeId"], "0x25 0x00")
        ]
        rebuilt = apply_delta(before, json.loads(json.dumps(delta)))
        assert rebuilt == after
        assert rebuilt["fieldStatus"]["main.freqHz"]["source"] == {
            **before["fieldStatus"]["main.freqHz"]["source"],
            "nativeId": "0x25 0x00",
        }

    def test_one_field_status_leaf_change_is_a_sub_kilobyte_delta(self):
        encoder = DeltaEncoder()
        times = {index: 1.0 for index in range(110)}
        before = _station_state(times)
        encoder.encode(before)
        assert len(json.dumps(before["fieldStatus"])) > 30_000

        after = _station_state({**times, 7: 2.0})
        delta = encoder.encode(after)

        assert delta["set"] == [
            (["fieldStatus", "main.field7", "lastObservedMonotonic"], 2.0)
        ]
        assert len(json.dumps(delta)) < 1024

    def test_top_level_rendering_keeps_each_touched_value_whole(self):
        """Clients that did not ask for leaf deltas (Pro's RC-28 dispatcher
        replaces its ``fieldStatus`` and ``connection`` from ``changed``)."""
        encoder = DeltaEncoder()
        times = {index: 1.0 for index in range(3)}
        encoder.encode({**_station_state(times), "sub": {"freqHz": 7_074_000}})
        after = _station_state({**times, 1: 2.0})
        after["connection"]["liveness"] = {"lastFrameAgeMs": 5}
        del after["main"]["mode"]

        delta = top_level_delta(encoder.encode(after), after)

        assert delta["changed"] == {
            "fieldStatus": after["fieldStatus"],
            "connection": after["connection"],
            "main": after["main"],
        }
        assert delta["removed"] == ["sub"]
        assert not {"deltaVersion", "set", "unset"} & delta.keys()

    def test_full_envelope_renders_unchanged(self):
        envelope = DeltaEncoder().encode({"freq": 1})
        assert top_level_delta(envelope, {"freq": 1}) is envelope

    def test_apply_keeps_untouched_subtrees_and_the_input_intact(self):
        state = _station_state({0: 1.0, 1: 1.0})
        before = copy.deepcopy(state)
        delta = {
            "type": "delta",
            "deltaVersion": 2,
            "set": [[["fieldStatus", "main.field1", "lastObservedMonotonic"], 2.0]],
        }

        result = apply_delta(state, delta)

        assert state == before
        assert result["fieldStatus"]["main.field1"]["lastObservedMonotonic"] == 2.0
        assert result["main"] is state["main"]
        assert (
            result["fieldStatus"]["main.field0"] is state["fieldStatus"]["main.field0"]
        )

    def test_leaf_path_through_a_non_object_is_rejected(self):
        delta = {"type": "delta", "deltaVersion": 2, "set": [[["main", "x"], 1]]}
        with pytest.raises(ValueError, match="Invalid delta path"):
            apply_delta({"main": 5}, delta)

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            ({"a": {"b": 1, "c": 2}}, {"a": {"b": 1}}),
            ({"a": {"b": 1}}, {"a": {"b": {"deep": [1, 2]}}}),
            ({"a": {"b": {"deep": 1}}}, {"a": {"b": None}}),
            ({"a": 1}, {"a": {"b": 1}}),
            ({"a": {"b": 1}}, {"a": [1]}),
            ({"a": {"b": [1, 2]}}, {"a": {"b": [1, 3]}, "z": {}}),
            ({"a": {"b": {"c": 1}}, "y": 1}, {"a": {"b": {}}}),
        ],
    )
    def test_contract_round_trip(self, before, after):
        """Encode, validate against the wire contract, send as JSON, apply."""
        encoder = DeltaEncoder()
        encoder.encode(before)
        wire = json.loads(json.dumps(encoder.encode(after, state_revision=3)))

        envelope = StateUpdateEnvelope.model_validate(wire)

        assert envelope.deltaVersion == 2
        assert envelope.changed is None
        assert apply_delta(before, wire) == after
        assert apply_delta(before, top_level_delta(wire, after)) == after


def test_frame_contract_has_the_wire_schema_keys() -> None:
    """``StateUpdateFrame`` (the encoder's typed frame) and the published
    ``StateUpdateEnvelope`` schema name the same keys, required alike."""
    schema_required = {
        name
        for name, field in StateUpdateEnvelope.model_fields.items()
        if field.is_required()
    }

    assert StateUpdateFrame.__annotations__.keys() == (
        StateUpdateEnvelope.model_fields.keys()
    )
    assert StateUpdateFrame.__required_keys__ == schema_required


@pytest.mark.parametrize(
    "operation",
    [
        [42, 1],
        [[42], 1],
        [["main", 0], 1],
        [["main"]],
    ],
)
def test_wire_schema_rejects_a_set_operation_without_a_key_path(
    operation: list[Any],
) -> None:
    frame = {
        "type": "delta",
        "revision": 1,
        "transportSeq": 1,
        "deltaVersion": 2,
        "set": [operation],
    }

    with pytest.raises(pydantic.ValidationError):
        StateUpdateEnvelope.model_validate(frame)


def _store_observation(at: float) -> Observation:
    return Observation(
        path=FieldPath.active("0", "freq_mode", "freq_hz"),
        value=14_074_000,
        source=SourceMetadata(source="poll_response", provider="test"),
        timestamp_monotonic=at,
        max_age=None,
    )


def _drain(queue: BoundedQueue[dict[str, Any]]) -> list[dict[str, Any]]:
    envelopes = []
    while not queue.empty():
        event = queue.get_nowait()
        assert event["type"] == "state_update"
        envelopes.append(event["data"])
    return envelopes


def test_each_control_queue_gets_the_rendering_it_registered_for() -> None:
    """One shared diff: a leaf queue gets set/unset, a default queue whole values."""
    srv = WebServer(None, WebConfig(radio_model="IC-7610"))
    leaf_queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=16)
    default_queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=16)
    leaf_state = srv.register_control_event_queue(leaf_queue, leaf_deltas=True)["data"]
    default_state = srv.register_control_event_queue(default_queue)["data"]
    srv.command_state_store.apply(_store_observation(1.0))
    srv._last_state_broadcast = 0.0  # noqa: SLF001
    srv._broadcast_state_update()  # noqa: SLF001
    for envelope in _drain(leaf_queue):
        leaf_state = apply_delta(leaf_state, envelope)
    for envelope in _drain(default_queue):
        default_state = apply_delta(default_state, envelope)

    # A re-read of the same value: only its freshness stamp and counters move.
    srv.command_state_store.apply(_store_observation(2.0))
    srv._last_state_broadcast = 0.0  # noqa: SLF001
    srv._broadcast_state_update()  # noqa: SLF001
    [leaf] = _drain(leaf_queue)
    [default] = _drain(default_queue)

    paths = [path for path, _value in leaf["set"]]
    assert ["fieldStatus", "main.freqHz", "lastObservedMonotonic"] in paths
    assert all(path[:2] != ["fieldStatus", "main.mode"] for path in paths)
    assert "changed" not in leaf
    assert (
        default["changed"]["fieldStatus"]
        == apply_delta(leaf_state, leaf)["fieldStatus"]
    )
    assert len(json.dumps(default)) > 10 * len(json.dumps(leaf))
    assert apply_delta(leaf_state, leaf) == apply_delta(default_state, default)

    srv.unregister_control_event_queue(leaf_queue)
    assert leaf_queue not in srv._leaf_delta_queues  # noqa: SLF001


async def _state_frames(
    ws: aiohttp.ClientWebSocketResponse, count: int
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    async with asyncio.timeout(30.0):
        while len(frames) < count:
            message = json.loads((await ws.receive()).data)
            if message["type"] == "state_update":
                frames.append(message["data"])
    return frames


async def test_state_delta_query_selects_leaf_deltas_over_the_real_socket() -> None:
    """``/api/v1/ws?state_delta=2`` gets leaf deltas; a bare URL keeps the old ones."""
    stack = await soak_driver._start_simulator_stack(  # noqa: SLF001
        model="IC-7300", enable_audio=False
    )
    ws_base = f"ws://127.0.0.1:{stack.web_port}/api/v1/ws"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_base + "?state_delta=2") as leaf_ws:
                leaf = await _state_frames(leaf_ws, 6)
            async with session.ws_connect(ws_base) as default_ws:
                default = await _state_frames(default_ws, 6)
    finally:
        await stack.aclose()

    for frames in (leaf, default):
        assert frames[0]["type"] == "full"
        state = frames[0]["data"]
        for frame in frames[1:]:
            StateUpdateEnvelope.model_validate(frame)
            state = apply_delta(state, frame)
        rebuilt = {"type": "full", "revision": 0, "transportSeq": 0, "data": state}
        StateUpdateEnvelope.model_validate(rebuilt)
        assert state.keys() == frames[0]["data"].keys()
    assert {frame.get("deltaVersion") for frame in leaf[1:]} == {2}
    assert all("changed" in frame for frame in default[1:])


def test_copy_state_tree_rebuilds_every_container() -> None:
    """MOR-4211: the wire-state copy isolates nested dicts, lists and tuples."""
    state: dict[str, Any] = {
        "freq": 14_074_000,
        "fieldStatus": {
            "ptt": {"lastObservedMonotonic": 1.5, "quality": ["confirmed"]}
        },
        "pairs": ({"a": [1]},),
        "mode": None,
    }

    copied = copy_state_tree(state)

    assert copied == state
    copied["fieldStatus"]["ptt"]["quality"].append("changed")
    copied["fieldStatus"]["ptt"]["lastObservedMonotonic"] = 2.0
    copied["pairs"][0]["a"].append(2)
    assert state == {
        "freq": 14_074_000,
        "fieldStatus": {
            "ptt": {"lastObservedMonotonic": 1.5, "quality": ["confirmed"]}
        },
        "pairs": ({"a": [1]},),
        "mode": None,
    }


def test_encoder_baseline_is_not_the_caller_body() -> None:
    """MOR-4211: mutating an encoded body afterwards cannot hide the next change."""
    encoder = DeltaEncoder()
    body: dict[str, Any] = {"fieldStatus": {"ptt": {"lastObservedMonotonic": 1.0}}}
    encoder.encode(body)
    body["fieldStatus"]["ptt"]["lastObservedMonotonic"] = 2.0

    delta = encoder.encode({"fieldStatus": {"ptt": {"lastObservedMonotonic": 2.0}}})

    assert delta["set"] == [(["fieldStatus", "ptt", "lastObservedMonotonic"], 2.0)]
