from typing import cast

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


class _Trap:
    def __init__(self, result: bool | None) -> None:
        self.result = result

    def __eq__(self, other: object) -> bool:
        if self.result is None:
            raise AssertionError("scope equality must not run")
        return self.result


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

    scope = {"source": "websocket", "session_id": "ws-a"}
    assert service.terminate_active_commands("control scope invalidated", **scope) == 4
    assert service.terminate_active_commands("different reason", **scope) == 0
    failed = [event for event in service.lifecycle_events() if event.state == "failed"]
    assert [(event.command_id, event.message) for event in failed] == [
        (command_id, "control scope invalidated") for command_id in ("a", "q", "s", "k")
    ]
    assert service.terminate_active_commands("http gone", session_id=None) == 1
    assert service.terminate_active_commands("socket gone", source="websocket") == 1
    for command_id in ("x", "y"):
        service.emit_lifecycle(_intent(command_id, "websocket", "ws-a"), "accepted")
    x_intent = _intent("x", "websocket", "ws-a")
    service.emit_lifecycle(x_intent, "acknowledged", details={"session_id": "alias"})
    nested: list[int] = []

    def reenter(event: object) -> None:
        nested.append(service.terminate_active_commands("nested", source="websocket"))

    unsubscribe = service.subscribe_lifecycle(reenter)  # type: ignore[arg-type]
    assert service.terminate_active_commands("outer", source="websocket") == 2
    assert nested == [0, 0]
    failed = [event for event in service.lifecycle_events() if event.state == "failed"]
    assert [event.command_id for event in failed[-2:]] == ["x", "y"]
    assert {event.message for event in failed[-2:]} == {"outer"}
    assert service.terminate_active_commands("alias", session_id="alias") == 0
    unsubscribe()
    service.emit_lifecycle(_intent("z", "websocket", "ws-a"), "accepted")
    before = service.lifecycle_events()
    for malformed in (_Trap(None), _Trap(True), object()):
        assert service.terminate_active_commands("bad", source=malformed) == 0  # type: ignore[arg-type]
        assert service.terminate_active_commands("bad", session_id=malformed) == 0
    assert service.lifecycle_events() == before


def test_terminate_active_commands_preserves_existing_terminal_states() -> None:
    for terminal in ("failed", "timed_out", "reconciled", "superseded"):
        service, _, _ = _service()
        intent = _intent(terminal, "websocket", "ws-a")
        service.emit_lifecycle(intent, "acknowledged")
        service.emit_lifecycle(intent, terminal, message="original")
        assert service.terminate_active_commands("invalidate") == 0
        assert len(service.lifecycle_events()) == 2


async def test_termination_clears_pending_and_late_readback_cannot_revive() -> None:
    service, store, clock = _service()
    intent = _intent("late", "websocket", "ws-a")
    await service.execute(intent)
    scope = {"source": "websocket", "session_id": "ws-a"}
    assert service.terminate_active_commands("provider replaced") == 1
    assert service.pending_overlays(**scope) == ()
    assert service.readback_expectations(**scope, command_id="late") == ()
    before = tuple(service.lifecycle_events())
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
    for index in range(128):
        intent = _intent(str(index), "websocket", "ws-a")
        service.emit_lifecycle(intent, "accepted")
    service.emit_lifecycle(_intent("0", "websocket", "ws-a"), "acknowledged")
    service.emit_lifecycle(_intent("128", "websocket", "ws-a"), "accepted")

    assert len(service._active_commands) == 128  # noqa: SLF001
    assert service.lifecycle_events()[-2].command_id == "0"
    assert service.lifecycle_events()[-2].state == "failed"
    service.terminate_active_commands("shutdown")
    assert service._active_commands == {}  # noqa: SLF001
