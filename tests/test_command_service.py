"""Backend-neutral command service behavior."""

from __future__ import annotations

from collections.abc import Sequence
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
        ("set_rf_gain", {"level": 111}, "receiver.0.operator_controls.rf_gain", 111),
        ("set_af_level", {"level": 87}, "receiver.0.operator_controls.af_level", 87),
        ("set_squelch", {"level": 42}, "receiver.0.operator_controls.squelch", 42),
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
        ("set_rf_power", {"level": 88}, "global.operator_controls.power_level", 88),
        ("set_power", {"level": 77}, "global.operator_controls.power_level", 77),
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
