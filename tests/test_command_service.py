"""Backend-neutral command service behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, cast

import pytest

from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    PendingOverlay,
    command_intent_from_request,
    command_response_observation,
)
from rigplane.core.exceptions import TimeoutError as RigplaneTimeoutError
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
        command_source="websocket",
        session_id="ws-a",
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
async def test_execute_emits_lifecycle_events_and_applies_response_observations() -> (
    None
):
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


def test_apply_observation_projects_relative_web_command_to_bound_vfo() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.configure_relative_vfo_retention(
        generation=1,
        max_age=30.0,
        coherence_window=5.0,
    )
    relative = (
        _observation(
            FieldPath.active("0", "freq_mode", "freq_hz"), 14_190_000, at=10.0
        ),
        _observation(FieldPath.active("0", "freq_mode", "mode"), "USB", at=10.1),
        _observation(
            FieldPath.unselected("0", "freq_mode", "freq_hz"),
            14_075_000,
            at=10.2,
        ),
        _observation(FieldPath.unselected("0", "freq_mode", "mode"), "USB", at=10.3),
    )
    store.apply_relative_vfo_observations(relative, generation=1)
    store.apply(_observation(FieldPath.active_slot("0"), "A", at=10.4))
    store.apply_relative_vfo_observations(relative, generation=1)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=store,
        clock=clock.now,
    )
    command = Observation(
        path=FieldPath.active("0", "freq_mode", "freq_hz"),
        value=14_130_000,
        source=SourceMetadata(
            source="command_response",
            provider="web_command",
            transport="websocket",
            command_source="websocket",
            session_id="ws-a",
        ),
        timestamp_monotonic=11.0,
        correlation_id="spectrum-drag",
    )

    changeset = service.apply_observation(command)
    snapshot = store.snapshot()

    assert {change.path for change in changeset.changes} == {
        command.path,
        FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz"),
    }
    assert snapshot.field(command.path).value == 14_130_000
    assert (
        snapshot.field(FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz")).value
        == 14_130_000
    )


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_execute_acknowledges_without_confirming_state_when_executor_has_no_observation() -> (
    None
):
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    service = CommandService(
        executor=FakeExecutor(observations=()),
        state_store=store,
        clock=clock.now,
    )

    result = await service.execute(_intent())

    assert _states(result.lifecycle_events) == [
        "accepted",
        "queued",
        "sent",
        "acknowledged",
    ]
    assert result.observation_changes == ()
    with pytest.raises(KeyError):
        store.snapshot().field(_freq_path())
    assert service.project_pending_values(
        source="websocket",
        session_id="ws-a",
        paths=(_freq_path(),),
    ) == {_freq_path(): 14_074_000}


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_execute_captures_generation_before_awaited_executor_result() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    store = StateStore()

    class DelayedExecutor:
        async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
            started.set()
            await release.wait()
            return CommandExecutionResult(
                observations=(
                    replace(
                        _observation(_freq_path(), 14_074_000, at=10.0),
                        provider_generation=store.provider_generation,
                    ),
                )
            )

    service = CommandService(executor=DelayedExecutor(), state_store=store)
    task = asyncio.create_task(service.execute(_intent()))
    await started.wait()
    store.begin_provider_generation()
    release.set()
    await task

    assert _states(service.lifecycle_events())[-1] == "acknowledged"
    assert service.pending_overlays(source="websocket", session_id="ws-a")
    assert _freq_path() not in {field.path for field in store.snapshot().fields}


@pytest.mark.parametrize("stale", (True, False), ids=("rejected", "current"))
def test_only_current_non_empty_changeset_reconciles_overlay(stale: bool) -> None:
    store = StateStore()
    generation = store.begin_provider_generation()
    service = CommandService(executor=FakeExecutor(), state_store=store)
    intent = _intent()
    service.emit_lifecycle(intent, "accepted")
    service._record_intent_overlay(intent)  # noqa: SLF001
    if stale:
        store.begin_provider_generation()

    changeset = service.apply_observation(
        replace(
            _observation(_freq_path(), 14_074_000, at=10.0),
            provider_generation=generation,
        )
    )

    pending = service.pending_overlays(source="websocket", session_id="ws-a")
    assert bool(changeset.observed_paths) is not stale
    assert bool(pending) is stale
    assert ("reconciled" in _states(service.lifecycle_events())) is not stale


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
    assert (
        service.pending_overlays(
            source="websocket",
            session_id="ws-a",
            command_id="cmd-1",
            path=freq,
        )[0].value
        == 14_074_000
    )


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

    first = service.apply_observation(_observation(_freq_path(), 14_074_000, at=30.5))
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


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_core_timeout_error_is_classified_as_timed_out() -> None:
    clock = FreshnessClock(start=40.5)
    store = StateStore(freshness_clock=clock)
    service = CommandService(
        executor=FakeExecutor(fail=RigplaneTimeoutError("backend timed out")),
        state_store=store,
        clock=clock.now,
    )

    with pytest.raises(RigplaneTimeoutError, match="backend timed out"):
        await service.execute(_intent(command_id="cmd-core-timeout"))

    assert _states(service.lifecycle_events())[-1] == "timed_out"
    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_execute_failure_with_reused_command_id_expires_only_matching_scope() -> (
    None
):
    clock = FreshnessClock(start=41.0)
    service = CommandService(
        executor=FakeExecutor(fail=RuntimeError("radio rejected command")),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    service.record_pending_overlay(
        PendingOverlay(
            source="http",
            session_id=None,
            command_id="cmd-shared",
            path=freq,
            value=7_040_000,
            expires_at_monotonic=42.0,
        )
    )

    with pytest.raises(RuntimeError, match="radio rejected command"):
        await service.execute(_intent(command_id="cmd-shared"))

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert service.pending_overlays(source="http", session_id=None) == (
        PendingOverlay(
            source="http",
            session_id=None,
            command_id="cmd-shared",
            path=freq,
            value=7_040_000,
            expires_at_monotonic=42.0,
        ),
    )


def test_fail_command_with_reused_command_id_expires_only_matching_scope() -> None:
    clock = FreshnessClock(start=42.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    for source, session_id, value in (
        ("websocket", "ws-a", 14_074_000),
        ("websocket", "ws-b", 14_075_000),
        ("http", None, 14_076_000),
    ):
        service.record_pending_overlay(
            PendingOverlay(
                source=cast(CommandSource, source),
                session_id=session_id,
                command_id="cmd-shared",
                path=freq,
                value=value,
                expires_at_monotonic=43.0,
            )
        )

    service.emit_lifecycle(
        _intent(command_id="cmd-shared", session_id="ws-a"), "queued"
    )
    service.emit_lifecycle(
        _intent(command_id="cmd-shared", session_id="ws-b"),
        "queued",
    )
    service.emit_lifecycle(
        _intent(command_id="cmd-shared", source="http", session_id=None),
        "queued",
    )

    assert service.fail_command(
        "cmd-shared",
        message="radio rejected command",
        source="websocket",
        session_id="ws-a",
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert service.pending_overlays(source="websocket", session_id="ws-b") == (
        PendingOverlay(
            source="websocket",
            session_id="ws-b",
            command_id="cmd-shared",
            path=freq,
            value=14_075_000,
            expires_at_monotonic=43.0,
        ),
    )
    assert service.pending_overlays(source="http", session_id=None) == (
        PendingOverlay(
            source="http",
            session_id=None,
            command_id="cmd-shared",
            path=freq,
            value=14_076_000,
            expires_at_monotonic=43.0,
        ),
    )


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

    assert (
        service.project_pending_values(
            source="public_api",
            session_id=None,
            paths=(_freq_path(),),
        )
        == {}
    )
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


def test_same_path_value_across_command_ids_reconciles_only_correlated_command() -> (
    None
):
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

    assert (
        service.pending_overlays(
            source="websocket",
            session_id="ws-a",
            command_id="cmd-a",
        )
        == ()
    )
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


def test_uncorrelated_duplicate_observation_does_not_reconcile_pending_overlay() -> (
    None
):
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

    assert service.pending_overlays(source="websocket", session_id="ws-a") == (overlay,)
    assert [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ] == []


def test_uncorrelated_same_value_boolean_does_not_reconcile_overlay() -> None:
    # MOR-435: for low-cardinality fields (booleans) value-equality is a weak
    # signal. A same-value, same-path, same-session observation that is NOT
    # correlated to the command must not prematurely confirm the overlay.
    clock = FreshnessClock(start=57.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    split = FieldPath.global_("tx_state", "split")
    overlay = PendingOverlay(
        source="websocket",
        session_id="ws-a",
        command_id="cmd-split",
        path=split,
        value=True,
        expires_at_monotonic=59.0,
    )
    service.record_pending_overlay(overlay)

    # Coincidental boolean readback carrying the same value but no correlation.
    service.apply_observation(_observation(split, True, at=57.2, correlation_id=None))

    assert service.pending_overlays(source="websocket", session_id="ws-a") == (overlay,)
    assert [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ] == []

    # The legitimately correlated readback still reconciles the boolean overlay.
    service.apply_observation(
        _observation(split, True, at=57.4, correlation_id="cmd-split")
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    reconciled = [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ]
    assert [event.command_id for event in reconciled] == ["cmd-split"]


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


def test_correlated_receiver_zero_overlay_reconciles_main_readback_alias() -> None:
    clock = FreshnessClock(start=58.5)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    overlay_path = FieldPath.receiver("0", "freq_mode", "freq_hz")
    readback_path = FieldPath.active("main", "freq_mode", "freq_hz")
    service.record_pending_overlay(
        PendingOverlay(
            source="websocket",
            session_id="ws-a",
            command_id="cmd-a",
            path=overlay_path,
            value=14_074_000,
            expires_at_monotonic=59.0,
        )
    )

    service.apply_observation(
        Observation(
            path=readback_path,
            value=14_074_000,
            source=SourceMetadata(
                source="hamlib_response",
                provider="external_rigctld",
                transport="rigctld",
                command_source="websocket",
                session_id="ws-a",
            ),
            timestamp_monotonic=58.7,
            correlation_id="cmd-a",
        )
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert service.lifecycle_events()[-1].state == "reconciled"
    assert service.lifecycle_events()[-1].command_id == "cmd-a"
    assert service.lifecycle_events()[-1].details["session_id"] == "ws-a"


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_expired_overlay_still_reconciles_correlated_external_rigctld_readback() -> (
    None
):
    clock = FreshnessClock(start=58.6)
    service = CommandService(
        executor=FakeExecutor(observations=()),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    command_id = "cmd-expired-rigctld"
    await service.execute(_intent(command_id=command_id, session_id="ws-a"))
    clock.advance(2.01)
    assert (
        service.pending_overlays(
            source="websocket",
            session_id="ws-a",
            command_id=command_id,
        )
        == ()
    )

    service.apply_observation(
        Observation(
            path=FieldPath.active("main", "freq_mode", "freq_hz"),
            value=14_074_000,
            source=SourceMetadata(
                source="hamlib_response",
                provider="external_rigctld",
                transport="rigctld",
                command_source="websocket",
                session_id="ws-a",
            ),
            timestamp_monotonic=60.7,
            correlation_id=command_id,
        )
    )

    assert _states(
        event for event in service.lifecycle_events() if event.command_id == command_id
    ) == ["accepted", "queued", "sent", "acknowledged", "reconciled"]


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
@pytest.mark.asyncio  # type: ignore[untyped-decorator]
@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("command_source", "session_id"),
    (("http", None), ("websocket", "ws-b")),
)
async def test_expired_overlay_correlated_rigctld_readback_requires_matching_scope(
    command_source: CommandSource,
    session_id: str | None,
) -> None:
    clock = FreshnessClock(start=58.7)
    service = CommandService(
        executor=FakeExecutor(observations=()),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    command_id = "cmd-expired-wrong-scope"
    await service.execute(_intent(command_id=command_id, session_id="ws-a"))
    clock.advance(2.01)

    service.apply_observation(
        Observation(
            path=FieldPath.active("main", "freq_mode", "freq_hz"),
            value=14_074_000,
            source=SourceMetadata(
                source="hamlib_response",
                provider="external_rigctld",
                transport="rigctld",
                command_source=command_source,
                session_id=session_id,
            ),
            timestamp_monotonic=60.8,
            correlation_id=command_id,
        )
    )

    assert _states(
        event for event in service.lifecycle_events() if event.command_id == command_id
    ) == ["accepted", "queued", "sent", "acknowledged"]


def test_correlated_receiver_zero_overlay_does_not_alias_non_rigctld_readback() -> None:
    clock = FreshnessClock(start=58.8)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    overlay_path = FieldPath.receiver("0", "freq_mode", "freq_hz")
    readback_path = FieldPath.active("main", "freq_mode", "freq_hz")
    overlay = PendingOverlay(
        source="websocket",
        session_id="ws-a",
        command_id="cmd-a",
        path=overlay_path,
        value=14_074_000,
        expires_at_monotonic=59.0,
    )
    service.record_pending_overlay(overlay)

    service.apply_observation(
        Observation(
            path=readback_path,
            value=14_074_000,
            source=SourceMetadata(
                source="state_poller",
                provider="test_backend",
                transport="fake",
                command_source="websocket",
                session_id="ws-a",
            ),
            timestamp_monotonic=58.9,
            correlation_id="cmd-a",
        )
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == (overlay,)
    assert [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ] == []


def test_correlated_receiver_zero_overlay_does_not_alias_rigctld_ack_metadata() -> None:
    clock = FreshnessClock(start=58.8)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    overlay_path = FieldPath.receiver("0", "freq_mode", "freq_hz")
    readback_path = FieldPath.active("main", "freq_mode", "freq_hz")
    overlay = PendingOverlay(
        source="websocket",
        session_id="ws-a",
        command_id="cmd-a",
        path=overlay_path,
        value=14_074_000,
        expires_at_monotonic=59.0,
    )
    service.record_pending_overlay(overlay)

    service.apply_observation(
        Observation(
            path=readback_path,
            value=14_074_000,
            source=SourceMetadata(
                source="command_response",
                provider="external_rigctld",
                transport="rigctld",
                command_source="websocket",
                session_id="ws-a",
            ),
            timestamp_monotonic=58.9,
            correlation_id="cmd-a",
        )
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == (overlay,)
    assert [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ] == []


def test_same_command_id_reused_across_sources_requires_matching_source_metadata() -> (
    None
):
    clock = FreshnessClock(start=59.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    for source in ("websocket", "http"):
        service.record_pending_overlay(
            PendingOverlay(
                source=cast(CommandSource, source),
                session_id=None,
                command_id="cmd-shared",
                path=freq,
                value=14_074_000,
                expires_at_monotonic=60.0,
            )
        )

    service.apply_observation(
        Observation(
            path=freq,
            value=14_074_000,
            source=SourceMetadata(
                source="command_response",
                provider="test",
                transport="fake",
                command_source="websocket",
            ),
            timestamp_monotonic=59.2,
            correlation_id="cmd-shared",
        )
    )

    assert service.pending_overlays(source="websocket", session_id=None) == ()
    assert service.pending_overlays(source="http", session_id=None) == (
        PendingOverlay(
            source="http",
            session_id=None,
            command_id="cmd-shared",
            path=freq,
            value=14_074_000,
            expires_at_monotonic=60.0,
        ),
    )


def test_same_command_id_reused_across_sessions_requires_matching_session_metadata() -> (
    None
):
    clock = FreshnessClock(start=60.0)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    for session_id in ("ws-a", "ws-b"):
        service.record_pending_overlay(
            PendingOverlay(
                source="websocket",
                session_id=session_id,
                command_id="cmd-shared",
                path=freq,
                value=14_074_000,
                expires_at_monotonic=61.0,
            )
        )

    service.apply_observation(
        Observation(
            path=freq,
            value=14_074_000,
            source=SourceMetadata(
                source="command_response",
                provider="test",
                transport="fake",
                command_source="websocket",
                session_id="ws-a",
            ),
            timestamp_monotonic=60.2,
            correlation_id="cmd-shared",
        )
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert service.pending_overlays(source="websocket", session_id="ws-b") == (
        PendingOverlay(
            source="websocket",
            session_id="ws-b",
            command_id="cmd-shared",
            path=freq,
            value=14_074_000,
            expires_at_monotonic=61.0,
        ),
    )


def test_sessionless_observation_does_not_reconcile_session_scoped_overlay() -> None:
    clock = FreshnessClock(start=60.5)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(freshness_clock=clock),
        clock=clock.now,
    )
    freq = _freq_path()
    overlays = tuple(
        PendingOverlay(
            source="websocket",
            session_id=session_id,
            command_id="cmd-shared",
            path=freq,
            value=14_074_000,
            expires_at_monotonic=61.5,
        )
        for session_id in ("ws-a", "ws-b")
    )
    for overlay in overlays:
        service.record_pending_overlay(overlay)

    service.apply_observation(
        Observation(
            path=freq,
            value=14_074_000,
            source=SourceMetadata(
                source="command_response",
                provider="test",
                transport="fake",
                command_source="websocket",
                session_id=None,
            ),
            timestamp_monotonic=60.7,
            correlation_id="cmd-shared",
        )
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == (
        overlays[0],
    )
    assert service.pending_overlays(source="websocket", session_id="ws-b") == (
        overlays[1],
    )
    assert [
        event for event in service.lifecycle_events() if event.state == "reconciled"
    ] == []


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


def test_command_intent_from_web_set_freq_request_targets_receiver_state() -> None:
    intent = command_intent_from_request(
        "set_freq",
        {"freq": 14_074_000, "receiver": 1, "session_id": "ws-a"},
        source="websocket",
        command_id="ws-123",
    )

    assert intent.id == "ws-123"
    assert intent.name == "set_freq"
    assert intent.source == "websocket"
    assert intent.params["freq_hz"] == 14_074_000
    assert intent.params["receiver"] == 1
    assert intent.params["session_id"] == "ws-a"
    assert str(intent.target) == "receiver.1.freq_mode.freq_hz"
    assert intent.pending_policy == "scoped"
    assert intent.expected_observations == (intent.target,)


def test_command_response_observation_uses_command_response_source() -> None:
    intent = command_intent_from_request(
        "set_mode",
        {"mode": "USB", "filter_width": 2, "receiver": 0},
        source="rigctld",
        command_id="rig-1",
    )

    observation = command_response_observation(
        intent,
        timestamp_monotonic=42.0,
        provider="rigctld",
    )

    assert str(observation.path) == "receiver.0.freq_mode.mode"
    assert observation.value == "USB"
    assert observation.source.source == "command_response"
    assert observation.source.provider == "rigctld"
    assert observation.source.command_source == "rigctld"
    assert observation.source.session_id is None
    assert observation.correlation_id == "rig-1"


def test_command_response_observation_carries_session_metadata() -> None:
    intent = command_intent_from_request(
        "set_freq",
        {"freq": 14_074_000, "receiver": 0, "session_id": "ws-a"},
        source="websocket",
        command_id="ws-1",
    )

    observation = command_response_observation(
        intent,
        timestamp_monotonic=43.0,
        provider="web_poller",
    )

    assert observation.source.command_source == "websocket"
    assert observation.source.session_id == "ws-a"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("name", "params", "expected_path", "expected_value"),
    [
        ("set_filter", {"filter_num": 2}, "receiver.0.freq_mode.filter_num", 2),
        ("set_filter", {"filter": "FIL3"}, "receiver.0.freq_mode.filter_num", 3),
        ("set_ptt", {"on": True}, "global.tx_state.ptt", True),
        ("ptt", {"state": True}, "global.tx_state.ptt", True),
        ("ptt_on", {}, "global.tx_state.ptt", True),
        ("ptt_off", {}, "global.tx_state.ptt", False),
        (
            "set_rf_gain",
            {"level": 111},
            "receiver.0.operator_controls.rf_gain",
            111 / 255,
        ),
        (
            "set_af_level",
            {"level": 87},
            "receiver.0.operator_controls.af_level",
            87 / 255,
        ),
        (
            "set_squelch",
            {"level": 42},
            "receiver.0.operator_controls.squelch",
            42 / 255,
        ),
        ("set_att", {"db": 12}, "receiver.0.operator_controls.att", 12),
        ("set_attenuator", {"level": 18}, "receiver.0.operator_controls.att", 18),
        ("set_preamp", {"level": 2}, "receiver.0.operator_controls.preamp", 2),
        ("set_nb", {"on": True}, "receiver.0.operator_toggles.nb", True),
        ("set_nr", {"on": False}, "receiver.0.operator_toggles.nr", False),
        (
            "set_digisel",
            {"on": True},
            "receiver.0.operator_toggles.digisel",
            True,
        ),
        (
            "set_ip_plus",
            {"on": False},
            "receiver.0.operator_toggles.ipplus",
            False,
        ),
        (
            "set_pbt_inner",
            {"level": 140},
            "receiver.0.operator_controls.pbt_inner",
            140,
        ),
        (
            "set_pbt_outer",
            {"level": 116},
            "receiver.0.operator_controls.pbt_outer",
            116,
        ),
        ("set_powerstat", {"on": False}, "global.tx_state.power_on", False),
        (
            "set_rf_power",
            {"level": 88},
            "global.operator_controls.power_level",
            88 / 255,
        ),
        (
            "set_power",
            {"level": 77},
            "global.operator_controls.power_level",
            77 / 255,
        ),
        (
            "set_filter_width",
            {"width": 1500},
            "receiver.0.freq_mode.filter_width",
            1500,
        ),
        ("set_split", {"on": True}, "global.tx_state.split", True),
        ("set_rit", {"hz": 500}, "global.operator_controls.rit_freq", 500),
        ("set_xit", {"hz": -250}, "global.operator_controls.rit_freq", -250),
        ("set_vfo", {"vfo": "B"}, "receiver.0.vfo.active_slot", "B"),
        (
            "set_vfo",
            {"vfo": "VFOB", "receiver_count": 2},
            "global.slow_state.active",
            "SUB",
        ),
    ],
)
def test_command_intent_targets_observable_production_write_paths(
    name: str,
    params: dict[str, object],
    expected_path: str,
    expected_value: object,
) -> None:
    intent = command_intent_from_request(
        name,
        params,
        source="http",
        command_id=f"cmd-{name}",
    )

    assert str(intent.target) == expected_path
    assert intent.pending_policy == "scoped"
    observation = command_response_observation(
        intent,
        timestamp_monotonic=70.0,
        provider="test",
    )
    assert observation.value == expected_value


@pytest.mark.parametrize(
    ("vfo", "receiver_count", "expected_slot", "expected_receiver"),
    [
        ("A", 1, "A", None),
        ("B", 1, "B", None),
        ("B", 2, "B", None),
        ("MAIN", 2, None, "MAIN"),
        ("SUB", 2, None, "SUB"),
        ("VFOA", 1, "A", None),
        ("VFOB", 1, "B", None),
        ("VFOA", 2, None, "MAIN"),
        ("VFOB", 2, None, "SUB"),
    ],
)
def test_vfo_intent_keeps_slot_and_receiver_namespaces_separate(
    vfo: str,
    receiver_count: int,
    expected_slot: str | None,
    expected_receiver: str | None,
) -> None:
    intent = command_intent_from_request(
        "set_vfo",
        {"vfo": vfo, "receiver_count": receiver_count},
        source="websocket",
        command_id=f"vfo-{vfo}-{receiver_count}",
    )

    assert intent.params.get("active_slot") == expected_slot
    assert intent.params.get("active") == expected_receiver


@pytest.mark.asyncio
async def test_set_level_command_overlay_keeps_raw_byte_value_for_current_contract() -> (
    None
):
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(),
    )
    intent = command_intent_from_request(
        "set_level",
        {"level": "AF", "value": 0.5},
        source="rigctld",
        command_id="rigctld-set-af",
        session_id="client-a",
    )
    path = FieldPath.receiver("0", "operator_controls", "af_level")

    assert intent.params["af_level"] == 128
    assert (
        command_response_observation(
            intent,
            timestamp_monotonic=70.0,
            provider="test",
        ).value
        == 128
    )

    await service.execute(intent)

    assert service.project_pending_values(
        source="rigctld",
        session_id="client-a",
        paths=(path,),
    ) == {path: 128}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params", "path"),
    [
        (
            "set_af_level",
            {"level": 128, "receiver": 0},
            FieldPath.receiver("0", "operator_controls", "af_level"),
        ),
        (
            "set_rf_gain",
            {"level": 64, "receiver": 1},
            FieldPath.receiver("1", "operator_controls", "rf_gain"),
        ),
        (
            "set_squelch",
            {"level": 32, "receiver": 0},
            FieldPath.receiver("0", "operator_controls", "squelch"),
        ),
        (
            "set_rf_power",
            {"level": 255},
            FieldPath.global_("operator_controls", "power_level"),
        ),
    ],
)
async def test_level_command_readback_expectations_are_normalized_but_params_stay_raw(
    name: str,
    params: dict[str, object],
    path: FieldPath,
) -> None:
    executor = FakeExecutor()
    clock = FreshnessClock(start=0.0)
    service = CommandService(
        executor=executor,
        state_store=StateStore(),
        clock=clock.now,
    )
    intent = command_intent_from_request(
        name,
        params,
        source="websocket",
        command_id=f"ws-{name}",
        session_id="client-a",
    )

    await service.execute(intent)

    assert executor.intents == [intent]
    assert executor.intents[0].params["level"] == params["level"]
    assert executor.intents[0].params[path.name] == params["level"]
    assert service.readback_expectations(
        source="websocket",
        session_id="client-a",
        command_id=f"ws-{name}",
    ) == (
        PendingOverlay(
            source="websocket",
            session_id="client-a",
            command_id=f"ws-{name}",
            path=path,
            value=cast(int, params["level"]) / 255,
            expires_at_monotonic=4.0,
        ),
    )


@pytest.mark.asyncio
async def test_set_squelch_command_response_reconciles_normalized_overlay() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    intent = command_intent_from_request(
        "set_squelch",
        {"level": 128, "receiver": 0},
        source="websocket",
        command_id="ws-squelch",
        session_id="client-a",
    )
    path = FieldPath.receiver("0", "operator_controls", "squelch")
    observation = command_response_observation(
        intent,
        timestamp_monotonic=10.0,
        provider="test",
    )
    service = CommandService(
        executor=FakeExecutor(observations=(observation,)),
        state_store=store,
        clock=clock.now,
    )

    result = await service.execute(intent)

    assert intent.params["level"] == 128
    assert intent.params["squelch"] == 128
    assert observation.value == 128 / 255
    assert store.snapshot().field(path).value == 128 / 255
    assert (
        service.pending_overlays(
            source="websocket",
            session_id="client-a",
            command_id="ws-squelch",
        )
        == ()
    )
    assert (
        service.readback_expectations(
            source="websocket",
            session_id="client-a",
            command_id="ws-squelch",
        )
        == ()
    )
    assert _states(result.lifecycle_events) == [
        "accepted",
        "queued",
        "sent",
        "acknowledged",
        "reconciled",
    ]


def test_normalized_level_command_expectation_matches_radio_scale() -> None:
    """MOR-334 regression (rf-gain set reverts to 0 / left edge).

    The web AF-level slider emits a normalized 0.0-1.0 ``level`` — this is
    ``set_af_level``'s genuine, documented frontend contract (unlike
    ``set_rf_gain``/``set_squelch``, which are raw-int-only; see MOR-1579
    below). The migration coerced the command param with a bare ``int()``,
    so a slider at 98% (``0.98``) collapsed to ``int(0.98) == 0`` and the
    StateStore expectation/overlay sat at the *opposite* end of the scale
    from the value the radio was actually driven to
    (``round(0.98 * 255) == 250``). The deferred readback (~0.98) then
    never matched the bogus ``0`` expectation, surfacing as the control
    snapping back to 0. The expected/overlay value must track the same raw
    scale the radio is set to via
    :func:`rigplane.core.command_service._af_level_from_param`.

    MOR-1579 follow-up: this test used to also parametrize over
    ``set_rf_gain``/``set_squelch`` with the same ``0.98`` float, which was
    itself an instance of the bug this PR fixes — those two intents are
    raw-int-only (PR #2491), so a float is never valid input for them at
    all, not "normalized 0.98". See
    ``test_raw_level_one_expectation_is_not_reinterpreted_as_full_scale``
    and ``test_squelch_float_level_rejected_not_silently_coerced`` below
    for their corrected coverage.
    """
    from rigplane.core.command_service import _af_level_from_param

    intent = command_intent_from_request(
        "set_af_level",
        {"level": 0.98, "receiver": 0},
        source="websocket",
        command_id="ws-set_af_level",
        session_id="client-a",
    )
    path = FieldPath.receiver("0", "operator_controls", "af_level")

    # Param is coerced to the raw scale the radio actually receives — not 0.
    radio_raw = _af_level_from_param(0.98)
    assert radio_raw == 250
    assert intent.params["af_level"] == radio_raw

    # The expectation/overlay value the readback reconciles against is the
    # normalized form of that same raw value (~0.98), not 0.0.
    observation = command_response_observation(
        intent,
        timestamp_monotonic=70.0,
        provider="test",
    )
    assert str(intent.target) == str(path)
    assert observation.value == pytest.approx(250 / 255)
    assert observation.value > 0.9


def test_normalized_af_level_fifty_round_trips_to_raw_fifty() -> None:
    """The public AF fixture stays normalized while the server owns raw conversion.

    set_af_level's frontend wire contract is normalized 0.0-1.0 (MOR-1579),
    so a float in that domain exercises ``_af_level_from_param`` directly
    (shared by both ``web/handlers/control.py``'s dispatch-time coercion
    and this module's StateStore expectation coercion).
    """
    from rigplane.core.command_service import _af_level_from_param

    assert _af_level_from_param(50 / 255) == 50


@pytest.mark.parametrize(
    ("name", "path_name"),
    [
        ("set_rf_gain", "rf_gain"),
        ("set_squelch", "squelch"),
    ],
)
def test_raw_level_one_expectation_is_not_reinterpreted_as_full_scale(
    name: str,
    path_name: str,
) -> None:
    """MOR-1579 regression (red-first leg): the magnitude heuristic used
    to treat any level in ``[0, 1]`` as normalized, so a legitimate raw
    level of ``1`` produced a StateStore expectation/overlay of ``1.0``
    (100%) instead of ``1/255`` (~0.4%) — the *opposite* end of the scale,
    reproducing the MOR-334 snap-back class in the other direction: the
    overlay would show 100% for the optimistic-update TTL, then "snap
    back" to ~0.4% once the real readback landed. ``set_rf_gain`` and
    ``set_squelch`` are raw-int-only (PR #2491) — an int level of ``1``
    must expectation-track as raw ``1`` (normalized ``1/255``), never as
    normalized ``1.0``.
    """
    from rigplane.core.command_service import _raw_int_level_from_param

    intent = command_intent_from_request(
        name,
        {"level": 1, "receiver": 0},
        source="websocket",
        command_id=f"ws-{name}",
        session_id="client-a",
    )
    path = FieldPath.receiver("0", "operator_controls", path_name)

    radio_raw = _raw_int_level_from_param(1)
    assert radio_raw == 1
    assert intent.params[path_name] == radio_raw

    observation = command_response_observation(
        intent,
        timestamp_monotonic=70.0,
        provider="test",
    )
    assert str(intent.target) == str(path)
    assert observation.value == pytest.approx(1 / 255)
    assert observation.value < 0.01


def test_squelch_float_level_rejected_not_silently_coerced() -> None:
    """MOR-1579: set_squelch is raw-int-only — a float is never a valid
    encoding for it, even one that superficially looks like a plausible
    normalized value. Type dispatch, never magnitude.
    """
    from rigplane.core.command_service import _raw_int_level_from_param

    with pytest.raises(ValueError, match="raw integer"):
        _raw_int_level_from_param(0.5)


def test_public_api_sync_squelch_actuation_value_is_not_reinterpreted() -> None:
    """MOR-1579 regression (red-first leg): ``_raw_level_from_param`` was
    NOT expectation-only — on the ``public_api`` sync ingress
    (:mod:`rigplane.runtime.sync`), its output is the *actual* value sent
    to the radio. ``_SyncCommandExecutor.execute`` reads
    ``intent.params["squelch"]`` directly and calls
    ``radio.set_squelch(int(params["squelch"]), ...)`` — it never looks at
    ``intent.params["level"]``. So ``sync.set_squelch(level=1)`` used to
    build an intent whose ``params["squelch"]`` was ``255`` (the same
    magnitude-heuristic bug as the wire-level fix, but unfixed on this
    ingress), driving the radio to full squelch instead of raw level 1.
    This builds the exact intent ``IcomRadio.set_squelch(1, receiver=0)``
    constructs and asserts the actuation value ``_SyncCommandExecutor``
    would read is raw ``1``, not ``255``.
    """
    intent = command_intent_from_request(
        "set_squelch",
        {"level": 1, "receiver": 0},
        source="public_api",
    )

    assert intent.params["squelch"] == 1


def test_power_level_float_expectation_matches_readback_scale() -> None:
    """MOR-1579 round 3 regression (red-first leg): the ``set_rf_power``
    expectation branch used to do a plain ``int(raw_level)``, so a
    normalized float level (e.g. ``0.4`` from the web power slider —
    ``control.py``'s ``_level_for_power`` treats ``set_rf_power`` as
    type-dispatched, same as ``set_af_level``) collapsed to
    ``int(0.4) == 0``. The StateStore overlay/expectation then sat at 0%
    for the optimistic-update TTL on *every single power-slider move*
    (not just a boundary value like the rf_gain/squelch raw-1 case),
    before jumping to the real readback — the same snap-back class this
    PR fixes elsewhere.

    Both backends' readbacks normalize to the same fraction ``v``
    regardless of unit (Icom CI-V as ``raw / 255``, Yaesu CAT as
    ``watts / max_watts`` — see
    ``backends/yaesu_cat/observations.py``'s ``_normalize_power_level``),
    so the coherent expectation for a float input is ``round(v * 255)``
    for *both* units, independent of ``native_power_unit`` — no radio
    object needed here.
    """
    intent = command_intent_from_request(
        "set_rf_power",
        {"level": 0.4},
        source="websocket",
        command_id="ws-set_rf_power",
        session_id="client-a",
    )
    path = FieldPath.global_("operator_controls", "power_level")

    # Param is coerced to the raw scale the radio actually receives — not 0.
    assert intent.params["power_level"] == 102  # round(0.4 * 255)

    # The expectation/overlay value the readback reconciles against is the
    # normalized form of that same raw value (~0.4), not 0.0.
    observation = command_response_observation(
        intent,
        timestamp_monotonic=70.0,
        provider="test",
    )
    assert str(intent.target) == str(path)
    assert observation.value == pytest.approx(102 / 255)
    assert observation.value == pytest.approx(0.4, abs=0.01)


@pytest.mark.parametrize(
    ("name", "level", "expected"),
    [
        ("set_rf_power", 50, 0.5),
        ("set_power", 50, 0.5),
        ("set_rf_power", 0.4, 102 / 255),
        ("set_power", 0.4, 102 / 255),
    ],
)
def test_power_expectations_share_canonical_and_alias_contract(
    name: str,
    level: int | float,
    expected: float,
) -> None:
    """A watts profile input applies only to integer power commands."""
    intent = command_intent_from_request(
        name,
        {"level": level},
        source="http",
        power_max_watts=100,
    )

    observation = command_response_observation(
        intent,
        timestamp_monotonic=70.0,
        provider="test",
    )
    assert observation.value == pytest.approx(expected)


@pytest.mark.parametrize("power_max_watts", [None, 0, -1])
def test_power_expectation_without_positive_watts_max_preserves_raw_scale(
    power_max_watts: int | None,
) -> None:
    """Absent or invalid watts metadata keeps the raw-255 contract."""
    intent = command_intent_from_request(
        "set_rf_power",
        {"level": 50},
        source="http",
        power_max_watts=power_max_watts,
    )

    observation = command_response_observation(
        intent,
        timestamp_monotonic=70.0,
        provider="test",
    )
    assert observation.value == pytest.approx(50 / 255)


@pytest.mark.asyncio
async def test_raw_external_rigctld_level_readback_normalizes_before_reconcile() -> (
    None
):
    clock = FreshnessClock(start=20.0)
    store = StateStore(freshness_clock=clock)
    service = CommandService(
        executor=FakeExecutor(),
        state_store=store,
        clock=clock.now,
    )
    intent = command_intent_from_request(
        "set_rf_power",
        {"level": 64},
        source="rigctld",
        command_id="rigctld-rf-power",
        session_id="client-a",
    )
    path = FieldPath.global_("operator_controls", "power_level")

    await service.execute(intent)
    assert intent.params["level"] == 64
    assert intent.params["power_level"] == 64

    service.apply_observation(
        Observation(
            path=path,
            value=64,
            source=SourceMetadata(
                source="hamlib_response",
                provider="external_rigctld",
                transport="rigctld",
                command_source="rigctld",
                session_id="client-a",
            ),
            timestamp_monotonic=20.1,
            correlation_id="rigctld-rf-power",
        )
    )

    assert store.snapshot().field(path).value == 64 / 255
    assert (
        service.pending_overlays(
            source="rigctld",
            session_id="client-a",
            command_id="rigctld-rf-power",
        )
        == ()
    )
    assert (
        service.readback_expectations(
            source="rigctld",
            session_id="client-a",
            command_id="rigctld-rf-power",
        )
        == ()
    )
    assert _states(
        event
        for event in service.lifecycle_events()
        if event.command_id == "rigctld-rf-power"
    ) == ["accepted", "queued", "sent", "acknowledged", "reconciled"]


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("name", "params", "expected"),
    [
        (
            "set_rit",
            {"hz": 500},
            (
                ("global.operator_controls.rit_freq", 500),
                ("global.tx_state.rit_on", True),
            ),
        ),
        (
            "set_rit",
            {"hz": 0},
            (
                ("global.operator_controls.rit_freq", 0),
                ("global.tx_state.rit_on", False),
            ),
        ),
        (
            "set_xit",
            {"hz": -250},
            (
                ("global.operator_controls.rit_freq", -250),
                ("global.tx_state.rit_tx", True),
            ),
        ),
        (
            "set_xit",
            {"hz": 0},
            (
                ("global.operator_controls.rit_freq", 0),
                ("global.tx_state.rit_tx", False),
            ),
        ),
    ],
)
async def test_rit_xit_intents_record_all_scoped_readback_targets(
    name: str,
    params: dict[str, object],
    expected: tuple[tuple[str, object], ...],
) -> None:
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(),
    )
    intent = command_intent_from_request(
        name,
        params,
        source="rigctld",
        command_id=f"rigctld-{name}",
        session_id="client-a",
    )
    paths = tuple(FieldPath.parse(path) for path, _value in expected)

    assert tuple(str(path) for path in intent.expected_observations) == tuple(
        path for path, _value in expected
    )

    await service.execute(intent)

    assert service.project_pending_values(
        source="rigctld",
        session_id="client-a",
        paths=paths,
    ) == {path: value for path, (_path, value) in zip(paths, expected)}
    assert (
        service.project_pending_values(
            source="rigctld",
            session_id="client-b",
            paths=paths,
        )
        == {}
    )
    assert {
        overlay.path: overlay.value
        for overlay in service.readback_expectations(
            source="rigctld",
            session_id="client-a",
            command_id=f"rigctld-{name}",
        )
    } == {path: value for path, (_path, value) in zip(paths, expected)}


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_multi_target_readback_reconciles_only_matching_rit_overlay() -> None:
    service = CommandService(
        executor=FakeExecutor(),
        state_store=StateStore(),
    )
    intent = command_intent_from_request(
        "set_rit",
        {"hz": 500},
        source="rigctld",
        command_id="rigctld-set-rit",
        session_id="client-a",
    )
    await service.execute(intent)

    service.apply_observation(
        Observation(
            path=FieldPath.global_("operator_controls", "rit_freq"),
            value=500,
            source=SourceMetadata(
                source="hamlib_response",
                provider="external_rigctld",
                transport="rigctld",
                command_source="rigctld",
                session_id="client-a",
            ),
            timestamp_monotonic=80.0,
            correlation_id="rigctld-set-rit",
        )
    )

    remaining = service.pending_overlays(
        source="rigctld",
        session_id="client-a",
        command_id="rigctld-set-rit",
    )
    assert [(str(overlay.path), overlay.value) for overlay in remaining] == [
        ("global.tx_state.rit_on", True)
    ]
