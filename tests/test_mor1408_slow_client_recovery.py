"""Adversarial delivery checks for MOR-1408 C's shared WebSocket baseline."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web._delta_encoder import apply_delta
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.server import WebServer


_SOURCE = SourceMetadata(source="test", provider="mor1408", transport="fake")


def _observe(server: WebServer, value: int) -> None:
    server.command_state_store.apply(
        Observation(
            path=FieldPath.active("0", "freq_mode", "freq_hz"),
            value=value,
            source=_SOURCE,
            timestamp_monotonic=time.monotonic(),
            provider_generation=server.command_state_store.provider_generation,
        )
    )
    server._last_state_broadcast = 0.0  # noqa: SLF001


def _event_data(queue: BoundedQueue[dict[str, Any]]) -> dict[str, Any]:
    event = queue.get_nowait()
    assert event["type"] == "state_update"
    return event["data"]


def _drain(queue: BoundedQueue[dict[str, Any]]) -> list[dict[str, Any]]:
    return [queue.get_nowait() for _ in range(queue.qsize())]


def _state_data(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [frame["data"] for frame in frames if frame["type"] == "state_update"]


def test_overflow_replaces_all_queued_state_frames_with_same_encode_full() -> None:
    server = WebServer(None)
    slow: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=3)
    initial = server.register_control_event_queue(slow)
    assert initial["type"] == "full"
    _observe(server, 14_070_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    baseline = _event_data(slow)
    assert baseline["type"] == "delta"

    _observe(server, 14_071_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    _observe(server, 14_072_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    _observe(server, 14_073_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    _observe(server, 14_074_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001

    recovery = _event_data(slow)
    assert recovery["type"] == "full"
    assert slow.empty()
    assert recovery["transportSeq"] == server._delta_encoder.revision  # noqa: SLF001

    _observe(server, 14_075_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    next_delta = _event_data(slow)
    assert next_delta["type"] == "delta"
    assert apply_delta(recovery["data"], next_delta) == server.build_public_state()


def test_mixed_queue_overflow_preserves_nonstate_order_after_recovery_full() -> None:
    server = WebServer(None)
    slow: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=4)
    server.register_control_event_queue(slow)
    slow.put_nowait({"type": "notification", "name": "a", "data": {}})
    _observe(server, 14_070_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    slow.put_nowait({"type": "event", "name": "b", "data": {}})
    _observe(server, 14_071_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    _observe(server, 14_072_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001

    frames = [slow.get_nowait() for _ in range(slow.qsize())]
    assert frames[0]["data"]["type"] == "full"
    assert [frame["name"] for frame in frames[1:]] == ["a", "b"]


def test_registration_and_subscribe_share_one_encoder_sequence_with_peers() -> None:
    server = WebServer(None)
    peer: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    first = server.register_control_event_queue(peer)
    assert first["type"] == "full"
    _observe(server, 14_070_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    peer_base = _event_data(peer)
    assert peer_base["type"] == "delta"

    newcomer: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    initial = server.register_control_event_queue(newcomer)
    peer_update = _event_data(peer)
    assert initial["type"] == "full"
    assert peer_update["type"] == "delta"
    assert initial["transportSeq"] == peer_update["transportSeq"]
    assert initial["data"] == apply_delta(
        apply_delta(first["data"], peer_base), peer_update
    )

    _observe(server, 14_071_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    peer_delta = _event_data(peer)
    newcomer_delta = _event_data(newcomer)
    assert peer_delta == newcomer_delta
    assert apply_delta(initial["data"], newcomer_delta) == server.build_public_state()

    server.enqueue_control_state_baseline(newcomer)
    resubscribe = _event_data(newcomer)
    peer_after_subscribe = _event_data(peer)
    assert resubscribe["type"] == "full"
    assert resubscribe["transportSeq"] == peer_after_subscribe["transportSeq"]


def test_repeated_overflow_keeps_only_the_latest_recovery_full() -> None:
    server = WebServer(None)
    slow: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=3)
    server.register_control_event_queue(slow)

    for freq in range(14_070_000, 14_070_007):
        _observe(server, freq)
        server._broadcast_state_update(force=True)  # noqa: SLF001

    states = _state_data(_drain(slow))
    assert len(states) == 1
    assert states[0]["type"] == "full"
    assert states[0]["data"]["main"]["freqHz"] == 14_070_006


def test_fast_and_two_independently_slow_clients_share_one_encode() -> None:
    server = WebServer(None)
    fast: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    slow_a: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=2)
    slow_b: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=2)
    server.register_control_event_queue(fast)
    server.register_control_event_queue(slow_a)
    server.register_control_event_queue(slow_b)
    _drain(fast)
    _drain(slow_a)
    _drain(slow_b)

    # Fill only the two slow queues with their own ordinary delta chains.
    for freq in (14_070_000, 14_071_000):
        _observe(server, freq)
        server._broadcast_state_update(force=True)  # noqa: SLF001
        _drain(fast)
    _observe(server, 14_072_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001

    fast_update = _event_data(fast)
    slow_a_update = _event_data(slow_a)
    slow_b_update = _event_data(slow_b)
    assert fast_update["type"] == "delta"
    assert slow_a_update["type"] == slow_b_update["type"] == "full"
    assert fast_update["transportSeq"] == slow_a_update["transportSeq"]
    assert fast_update["transportSeq"] == slow_b_update["transportSeq"]
    for key in (
        "stateContractVersion",
        "providerGeneration",
        "revision",
        "stateRevision",
        "freshnessRevision",
        "observationSeq",
    ):
        assert slow_a_update[key] == fast_update[key]
        assert slow_b_update[key] == fast_update[key]
    assert slow_a_update["data"] == slow_b_update["data"] == server.build_public_state()


def test_inflight_delta_precedes_recovery_full_after_overflow() -> None:
    server = WebServer(None)
    slow: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=3)
    direct_baseline = server.register_control_event_queue(slow)
    _observe(server, 14_070_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    in_flight = _event_data(slow)
    assert in_flight["type"] == "delta"

    for freq in (14_071_000, 14_072_000, 14_073_000, 14_074_000):
        _observe(server, freq)
        server._broadcast_state_update(force=True)  # noqa: SLF001

    recovery = _event_data(slow)
    assert recovery["type"] == "full"
    assert (
        apply_delta(direct_baseline["data"], in_flight)["main"]["freqHz"] == 14_070_000
    )
    assert recovery["data"]["main"]["freqHz"] == 14_074_000


def test_provider_generation_full_evicts_all_old_generation_state_frames() -> None:
    server = WebServer(None)
    slow: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=3)
    server.register_control_event_queue(slow)
    old_generation = server.command_state_store.provider_generation
    for freq in (14_070_000, 14_071_000, 14_072_000):
        _observe(server, freq)
        server._broadcast_state_update(force=True)  # noqa: SLF001

    new_generation = server.command_state_store.begin_provider_generation()
    _observe(server, 7_074_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001

    states = _state_data(_drain(slow))
    assert len(states) == 1
    assert states[0]["type"] == "full"
    assert states[0]["providerGeneration"] == new_generation
    assert states[0]["providerGeneration"] != old_generation
    assert states[0]["data"]["main"]["freqHz"] == 7_074_000


def test_handshake_b_to_h_to_d_missing_key_counterexample_converges() -> None:
    server = WebServer(None)
    peer: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    base = server.register_control_event_queue(peer)
    _observe(server, 14_070_000)  # B -> H changes the key.
    server._broadcast_state_update(force=True)  # noqa: SLF001
    peer_h = _event_data(peer)
    newcomer: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    newcomer_h = server.register_control_event_queue(newcomer)
    peer_handshake = _event_data(peer)
    _observe(server, 0)  # H -> D returns the changed key to B's value.
    server._broadcast_state_update(force=True)  # noqa: SLF001
    peer_d = _event_data(peer)
    newcomer_d = _event_data(newcomer)

    peer_state = apply_delta(apply_delta(base["data"], peer_h), peer_handshake)
    assert peer_state == newcomer_h["data"]
    assert apply_delta(peer_state, peer_d) == apply_delta(
        newcomer_h["data"], newcomer_d
    )
    assert apply_delta(newcomer_h["data"], newcomer_d) == server.build_public_state()


@pytest.mark.asyncio
async def test_initial_send_cancellation_unregisters_before_liveness() -> None:
    class CancelInitialSend:
        def __init__(self) -> None:
            self.calls = 0

        async def send_text(self, _payload: str) -> None:
            self.calls += 1
            if self.calls == 2:
                raise asyncio.CancelledError

        async def recv(self) -> tuple[int, bytes]:
            raise AssertionError(
                "receive loop must not start after initial-send cancellation"
            )

    server = WebServer(None)
    handler = ControlHandler(
        CancelInitialSend(),
        None,
        "test",
        "IC-TEST",
        server=server,
    )
    with pytest.raises(asyncio.CancelledError):
        await handler.run()

    assert handler._event_queue not in server._control_event_queues  # noqa: SLF001
    assert server.command_queue._live_sessions is None  # noqa: SLF001


def test_registration_encode_failure_is_transactional() -> None:
    server = WebServer(None)
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=3)

    def fail() -> tuple[object, dict[str, Any]]:
        raise RuntimeError("generation churn")

    server._build_public_state_for_delivery = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="generation churn"):
        server.register_control_event_queue(queue)
    assert queue not in server._control_event_queues  # noqa: SLF001


def test_disconnect_reconnect_has_no_recovery_structure_and_new_full() -> None:
    server = WebServer(None)
    old: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=2)
    server.register_control_event_queue(old)
    _observe(server, 14_070_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    _observe(server, 14_071_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    server.unregister_control_event_queue(old)

    new: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=2)
    baseline = server.register_control_event_queue(new)
    assert old not in server._control_event_queues  # noqa: SLF001
    assert new in server._control_event_queues  # noqa: SLF001
    assert baseline["type"] == "full"
    assert baseline["data"] == server.build_public_state()
    assert not any("recovery" in name for name in vars(server))


@pytest.mark.asyncio
async def test_subscribe_queues_full_then_dx_and_future_delta_converges() -> None:
    class Ws:
        async def send_text(self, _payload: str) -> None:
            raise AssertionError(
                "subscribe must not direct-send beside the sender loop"
            )

    server = WebServer(None)
    peer: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    peer_base = server.register_control_event_queue(peer)
    handler = ControlHandler(Ws(), None, "test", "IC-TEST", server=server)
    server.register_control_event_queue(handler._event_queue)  # noqa: SLF001
    peer_on_join = _event_data(peer)
    server._spot_buffer = SimpleNamespace(get_spots=lambda: [{"call": "K1ABC"}])  # type: ignore[assignment]  # noqa: SLF001

    _observe(server, 14_070_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    peer_h = _event_data(peer)
    await handler._send_state_snapshot()  # noqa: SLF001

    queued = _drain(handler._event_queue)  # noqa: SLF001
    assert queued[0]["type"] == "state_update"
    assert queued[0]["data"]["type"] == "full"
    assert queued[1] == {"type": "dx_spots", "spots": [{"call": "K1ABC"}]}
    peer_subscribe = _event_data(peer)
    assert queued[0]["data"]["transportSeq"] == peer_subscribe["transportSeq"]

    _observe(server, 14_071_000)
    server._broadcast_state_update(force=True)  # noqa: SLF001
    peer_d = _event_data(peer)
    handler_d = _event_data(handler._event_queue)  # noqa: SLF001
    peer_h_state = apply_delta(apply_delta(peer_base["data"], peer_on_join), peer_h)
    assert apply_delta(peer_h_state, peer_subscribe) == queued[0]["data"]["data"]
    assert peer_d == handler_d
    assert (
        apply_delta(queued[0]["data"]["data"], handler_d) == server.build_public_state()
    )
