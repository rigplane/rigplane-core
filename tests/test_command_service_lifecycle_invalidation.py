from typing import cast

import pytest

from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    CommandSource,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore


class _Executor:
    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        return CommandExecutionResult()


def _service() -> tuple[CommandService, StateStore, FreshnessClock]:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    service = CommandService(executor=_Executor(), state_store=store, clock=clock.now)
    return service, store, clock


def _intent(command_id: str, source: str, session_id: str | None) -> CommandIntent:
    path = FieldPath.active("main", "freq_mode", "freq_hz")
    return CommandIntent(
        id=command_id,
        name="set_freq",
        params={"freq_hz": 14_074_000, "session_id": session_id},
        source=cast(CommandSource, source),
        target=path,
        pending_policy="scoped",
        expected_observations=(path,),
    )


def test_terminate_active_commands_is_exact_scoped_and_idempotent() -> None:
    service, _, _ = _service()
    for state, command_id in zip(
        ("accepted", "queued", "sent", "acknowledged"),
        ("a", "q", "s", "k"),
        strict=True,
    ):
        service.emit_lifecycle(_intent(command_id, "websocket", "ws-a"), state)
    service.emit_lifecycle(_intent("a", "websocket", "ws-b"), "acknowledged")
    service.emit_lifecycle(_intent("a", "http", None), "acknowledged")

    assert (
        service.terminate_active_commands(
            "control scope invalidated", source="websocket", session_id="ws-a"
        )
        == 4
    )
    assert (
        service.terminate_active_commands(
            "different reason", source="websocket", session_id="ws-a"
        )
        == 0
    )
    failed = [event for event in service.lifecycle_events() if event.state == "failed"]
    assert [(event.command_id, event.message) for event in failed] == [
        (command_id, "control scope invalidated") for command_id in ("a", "q", "s", "k")
    ]
    assert service.terminate_active_commands("http gone", session_id=None) == 1
    assert service.terminate_active_commands("socket gone", source="websocket") == 1


def test_terminate_active_commands_preserves_existing_terminal_states() -> None:
    for terminal in ("failed", "timed_out", "reconciled", "superseded"):
        service, _, _ = _service()
        intent = _intent(terminal, "websocket", "ws-a")
        service.emit_lifecycle(intent, "acknowledged")
        service.emit_lifecycle(intent, terminal, message="original")
        assert service.terminate_active_commands("invalidate") == 0
        assert len(service.lifecycle_events()) == 2


@pytest.mark.asyncio
async def test_termination_clears_pending_and_late_readback_cannot_revive() -> None:
    service, store, clock = _service()
    intent = _intent("late", "websocket", "ws-a")
    await service.execute(intent)
    assert service.pending_overlays(source="websocket", session_id="ws-a")
    assert service.readback_expectations(
        source="websocket", session_id="ws-a", command_id="late"
    )

    assert service.terminate_active_commands("provider replaced") == 1
    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert (
        service.readback_expectations(
            source="websocket", session_id="ws-a", command_id="late"
        )
        == ()
    )
    before = tuple(service.lifecycle_events())
    clock.advance(0.1)
    service.apply_observation(
        Observation(
            path=intent.target,
            value=14_074_000,
            source=SourceMetadata(
                source="yaesu_poll_response",
                provider="yaesu_cat",
                transport="serial",
                command_source="websocket",
                session_id="ws-a",
            ),
            timestamp_monotonic=clock.now(),
            correlation_id="late",
        )
    )
    assert store.snapshot().field(intent.target).value == 14_074_000
    assert service.lifecycle_events() == before


def test_active_command_registry_is_bounded_and_terminal_entries_do_not_leak() -> None:
    service, _, _ = _service()
    for index in range(129):
        intent = _intent(str(index), "websocket", "ws-a")
        service.emit_lifecycle(intent, "accepted")

    assert len(service._active_commands) == 128  # noqa: SLF001
    assert service.lifecycle_events()[-2].command_id == "0"
    assert service.lifecycle_events()[-2].state == "failed"
    service.terminate_active_commands("shutdown")
    assert service._active_commands == {}  # noqa: SLF001
