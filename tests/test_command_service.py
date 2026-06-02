"""Backend-neutral command service behavior."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest

from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    PendingOverlay,
)
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    CommandLifecycleEvent,
    CommandSource,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore


class FakeExecutor:
    def __init__(
        self,
        *,
        observations: Sequence[Observation] = (),
        fail: Exception | None = None,
    ) -> None:
        self.observations = tuple(observations)
        self.fail = fail
        self.intents: list[CommandIntent] = []

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        self.intents.append(intent)
        if self.fail is not None:
            raise self.fail
        return CommandExecutionResult(observations=self.observations)


def _freq_path() -> FieldPath:
    return FieldPath.active("main", "freq_mode", "freq_hz")


def _mode_path() -> FieldPath:
    return FieldPath.active("main", "freq_mode", "mode")


def _source() -> SourceMetadata:
    return SourceMetadata(
        source="command_response",
        provider="test",
        transport="fake",
    )


def _observation(
    path: FieldPath,
    value: Any,
    *,
    at: float,
    correlation_id: str | None = "cmd-1",
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_source(),
        timestamp_monotonic=at,
        correlation_id=correlation_id,
    )


def _intent(
    *,
    command_id: str = "cmd-1",
    source: str = "websocket",
    session_id: str | None = "ws-a",
) -> CommandIntent:
    return CommandIntent(
        id=command_id,
        name="set_freq",
        params={
            "freq_hz": 14_074_000,
            "session_id": session_id,
        },
        source=cast(CommandSource, source),
        target=_freq_path(),
        priority="user",
        timeout=2.0,
        pending_policy="scoped",
        expected_observations=(_freq_path(),),
    )


def _states(events: Sequence[CommandLifecycleEvent]) -> list[str]:
    return [event.state for event in events]


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_execute_emits_lifecycle_events_and_applies_response_observations() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    executor = FakeExecutor(
        observations=(_observation(_freq_path(), 14_074_000, at=10.0),)
    )
    service = CommandService(executor=executor, state_store=store, clock=clock.now)

    result = await service.execute(_intent())

    assert executor.intents == [_intent()]
    assert _states(service.lifecycle_events()) == [
        "accepted",
        "queued",
        "sent",
        "acknowledged",
        "reconciled",
    ]
    assert result.observation_changes[0].changes[0].current == 14_074_000
    assert store.snapshot().field(_freq_path()).value == 14_074_000
    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()


def test_pending_overlays_are_projected_by_source_session_command_and_path() -> None:
    clock = FreshnessClock(start=20.0)
    store = StateStore(freshness_clock=clock)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=store,
        clock=clock.now,
    )
    freq = _freq_path()
    mode = _mode_path()

    service.record_pending_overlay(
        PendingOverlay(
            source="websocket",
            session_id="ws-a",
            command_id="cmd-1",
            path=freq,
            value=14_074_000,
            expires_at_monotonic=25.0,
        )
    )
    service.record_pending_overlay(
        PendingOverlay(
            source="websocket",
            session_id="ws-b",
            command_id="cmd-2",
            path=freq,
            value=7_074_000,
            expires_at_monotonic=25.0,
        )
    )
    service.record_pending_overlay(
        PendingOverlay(
            source="rigctld",
            session_id="rig-a",
            command_id="cmd-3",
            path=mode,
            value="USB",
            expires_at_monotonic=25.0,
        )
    )

    assert service.project_pending_values(
        source="websocket",
        session_id="ws-a",
        paths=(freq, mode),
    ) == {freq: 14_074_000}
    assert service.project_pending_values(
        source="websocket",
        session_id="ws-b",
        paths=(freq,),
    ) == {freq: 7_074_000}
    assert service.project_pending_values(
        source="rigctld",
        session_id="rig-a",
        paths=(mode,),
    ) == {mode: "USB"}
    assert service.pending_overlays(
        source="websocket",
        session_id="ws-a",
        command_id="cmd-1",
        path=freq,
    )[0].value == 14_074_000


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_late_matching_observation_reconciles_pending_overlay_once() -> None:
    clock = FreshnessClock(start=30.0)
    store = StateStore(freshness_clock=clock)
    service = CommandService(
        executor=FakeExecutor(observations=()),
        state_store=store,
        clock=clock.now,
    )

    await service.execute(_intent())
    assert service.project_pending_values(
        source="websocket",
        session_id="ws-a",
        paths=(_freq_path(),),
    ) == {_freq_path(): 14_074_000}

    first = service.apply_observation(
        _observation(_freq_path(), 14_074_000, at=30.5)
    )
    duplicate = service.apply_observation(
        _observation(_freq_path(), 14_074_000, at=30.6)
    )

    assert first.changes[0].current == 14_074_000
    assert duplicate.changes == ()
    assert _states(service.lifecycle_events()).count("reconciled") == 1
    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert store.snapshot().field(_freq_path()).value == 14_074_000


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_failed_and_timed_out_commands_expire_overlays() -> None:
    clock = FreshnessClock(start=40.0)
    store = StateStore(freshness_clock=clock)
    failed = CommandService(
        executor=FakeExecutor(fail=RuntimeError("radio rejected command")),
        state_store=store,
        clock=clock.now,
    )

    with pytest.raises(RuntimeError, match="radio rejected command"):
        await failed.execute(_intent(command_id="cmd-failed"))

    timeout = CommandService(
        executor=FakeExecutor(fail=TimeoutError("command timed out")),
        state_store=store,
        clock=clock.now,
    )

    with pytest.raises(TimeoutError, match="command timed out"):
        await timeout.execute(_intent(command_id="cmd-timeout"))

    assert _states(failed.lifecycle_events())[-1] == "failed"
    assert _states(timeout.lifecycle_events())[-1] == "timed_out"
    assert failed.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert timeout.pending_overlays(source="websocket", session_id="ws-a") == ()


def test_expired_pending_overlays_do_not_project_or_leak() -> None:
    clock = FreshnessClock(start=50.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    service.record_pending_overlay(
        PendingOverlay(
            source="public_api",
            session_id=None,
            command_id="cmd-1",
            path=_freq_path(),
            value=14_074_000,
            expires_at_monotonic=50.5,
        )
    )

    clock.advance(0.6)

    assert service.project_pending_values(
        source="public_api",
        session_id=None,
        paths=(_freq_path(),),
    ) == {}
    assert service.pending_overlays(source="public_api", session_id=None) == ()


def test_same_path_value_across_sessions_reconciles_only_correlated_overlay() -> None:
    clock = FreshnessClock(start=55.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    for session_id, command_id in (("ws-a", "cmd-a"), ("ws-b", "cmd-b")):
        service.record_pending_overlay(
            PendingOverlay(
                source="websocket",
                session_id=session_id,
                command_id=command_id,
                path=freq,
                value=14_074_000,
                expires_at_monotonic=56.0,
            )
        )

    service.apply_observation(
        _observation(freq, 14_074_000, at=55.2, correlation_id="cmd-a")
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert service.pending_overlays(source="websocket", session_id="ws-b") == (
        PendingOverlay(
            source="websocket",
            session_id="ws-b",
            command_id="cmd-b",
            path=freq,
            value=14_074_000,
            expires_at_monotonic=56.0,
        ),
    )
    reconciled = [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ]
    assert [event.command_id for event in reconciled] == ["cmd-a"]


def test_same_path_value_across_command_ids_reconciles_only_correlated_command() -> None:
    clock = FreshnessClock(start=56.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    for command_id in ("cmd-a", "cmd-b"):
        service.record_pending_overlay(
            PendingOverlay(
                source="websocket",
                session_id="ws-a",
                command_id=command_id,
                path=freq,
                value=14_074_000,
                expires_at_monotonic=57.0,
            )
        )

    service.apply_observation(
        _observation(freq, 14_074_000, at=56.2, correlation_id="cmd-a")
    )

    assert service.pending_overlays(
        source="websocket",
        session_id="ws-a",
        command_id="cmd-a",
    ) == ()
    assert service.pending_overlays(
        source="websocket",
        session_id="ws-a",
        command_id="cmd-b",
    ) == (
        PendingOverlay(
            source="websocket",
            session_id="ws-a",
            command_id="cmd-b",
            path=freq,
            value=14_074_000,
            expires_at_monotonic=57.0,
        ),
    )
    reconciled = [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ]
    assert [event.command_id for event in reconciled] == ["cmd-a"]


def test_uncorrelated_duplicate_observation_does_not_reconcile_pending_overlay() -> None:
    clock = FreshnessClock(start=57.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    overlay = PendingOverlay(
        source="websocket",
        session_id="ws-a",
        command_id="cmd-a",
        path=freq,
        value=14_074_000,
        expires_at_monotonic=58.0,
    )
    service.record_pending_overlay(overlay)

    service.apply_observation(
        _observation(freq, 14_074_000, at=57.2, correlation_id=None)
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == (
        overlay,
    )
    assert [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ] == []


def test_intended_correlated_observation_reconciles_pending_overlay() -> None:
    clock = FreshnessClock(start=58.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    service.record_pending_overlay(
        PendingOverlay(
            source="websocket",
            session_id="ws-a",
            command_id="cmd-a",
            path=freq,
            value=14_074_000,
            expires_at_monotonic=59.0,
        )
    )

    service.apply_observation(
        _observation(freq, 14_074_000, at=58.2, correlation_id="cmd-a")
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    reconciled = [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ]
    assert [event.command_id for event in reconciled] == ["cmd-a"]


def test_lifecycle_subscribers_observe_deterministic_events() -> None:
    clock = FreshnessClock(start=60.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    seen: list[CommandLifecycleEvent] = []

    unsubscribe = service.subscribe_lifecycle(seen.append)
    service.emit_lifecycle(_intent(), "queued", message="queued by web adapter")
    unsubscribe()
    service.emit_lifecycle(_intent(), "sent", message="sent by fake executor")

    assert _states(seen) == ["queued"]
    assert _states(service.lifecycle_events()) == ["queued", "sent"]
