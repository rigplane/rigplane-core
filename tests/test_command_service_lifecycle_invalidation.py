from typing import cast

from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    CommandSource,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore


class _Executor:
    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        return CommandExecutionResult()


class _Trap:
    def __init__(self, result: bool | None) -> None:
        self.result = result

    def __eq__(self, other: object) -> bool:
        assert self.result is not None, "scope equality must not run"
        return self.result


def _service() -> tuple[CommandService, StateStore]:
    store = StateStore()
    return CommandService(executor=_Executor(), state_store=store), store


def _intent(
    command_id: str, source: str, session_id: str | None = None
) -> CommandIntent:
    params: dict[str, object] = {"freq_hz": 14_074_000}
    if session_id is not None:
        params["session_id"] = session_id
    return CommandIntent(
        id=command_id,
        name="set_freq",
        params=params,
        source=cast(CommandSource, source),
        target=FieldPath.active("main", "freq_mode", "freq_hz"),
        pending_policy="scoped",
    )


def test_terminate_active_commands_is_exact_scoped_and_idempotent() -> None:
    service, _ = _service()
    emit = service.emit_lifecycle
    terminate = service.terminate_active_commands
    states = ("accepted", "queued", "sent", "acknowledged")
    for command_id, state in zip("aqsk", states, strict=True):
        emit(_intent(command_id, "websocket", "ws-a"), state)
    emit(_intent("a", "websocket", "ws-b"), "acknowledged")
    emit(_intent("a", "http"), "acknowledged")
    scope = {"source": "websocket", "session_id": "ws-a"}
    assert terminate("control scope invalidated", **scope) == 4
    assert terminate("different reason", **scope) == 0
    failed = [event for event in service.lifecycle_events() if event.state == "failed"]
    expected = [(command_id, "control scope invalidated") for command_id in "aqsk"]
    assert [(event.command_id, event.message) for event in failed] == expected
    assert terminate("http gone", session_id=None) == 1
    assert terminate("socket gone", source="websocket") == 1
    conflict = {"session_id": "other"}
    public = _intent("p", "public_api")
    emit(public, "accepted")
    emit(public, "acknowledged", details=conflict)
    assert terminate("public gone", source="public_api") == 1
    other = emit(_intent("p", "public_api", "other"), "accepted")
    emit(public, "acknowledged", details=conflict)
    assert service._active_commands[("public_api", "other", "p")] is other  # noqa: SLF001
    for command_id in ("x", "y"):
        emit(_intent(command_id, "websocket", "ws-a"), "accepted")
    x_intent = _intent("x", "websocket", "ws-a")
    emit(x_intent, "acknowledged", details={"session_id": "alias"})

    def reenter(event: object) -> None:
        assert terminate("nested", source="websocket") == 0

    unsubscribe = service.subscribe_lifecycle(reenter)  # type: ignore[arg-type]
    assert terminate("outer", source="websocket") == 2
    failed = [event for event in service.lifecycle_events() if event.state == "failed"]
    assert [event.command_id for event in failed[-2:]] == ["x", "y"]
    assert {event.message for event in failed[-2:]} == {"outer"}
    assert terminate("alias", session_id="alias") == 0
    unsubscribe()
    emit(_intent("z", "websocket", "ws-a"), "accepted")
    for malformed in (_Trap(None), _Trap(True), object()):
        assert terminate("bad", source=malformed) == 0  # type: ignore[arg-type]
        assert terminate("bad", session_id=malformed) == 0


def test_terminate_active_commands_preserves_existing_terminal_states() -> None:
    for terminal in ("failed", "timed_out", "reconciled", "superseded"):
        service, _ = _service()
        intent = _intent(terminal, "websocket", "ws-a")
        service.emit_lifecycle(intent, "acknowledged")
        service.emit_lifecycle(intent, terminal, message="original")
        assert service.terminate_active_commands("invalidate") == 0


async def test_termination_clears_pending_and_late_readback_cannot_revive() -> None:
    service, store = _service()
    intent = _intent("late", "websocket", "ws-a")
    await service.execute(intent)
    scope = {"source": "websocket", "session_id": "ws-a"}
    assert service.terminate_active_commands("provider replaced") == 1
    assert service.pending_overlays(**scope) == ()
    assert service.readback_expectations(**scope, command_id="late") == ()
    before = tuple(service.lifecycle_events())
    source = SourceMetadata(
        "yaesu_poll_response",
        "yaesu_cat",
        "serial",
        command_source="websocket",
        session_id="ws-a",
    )
    observation = Observation(
        intent.target, 14_074_000, source, 10.0, correlation_id="late"
    )
    service.apply_observation(observation)
    assert store.snapshot().field(intent.target).value == 14_074_000
    assert service.lifecycle_events() == before


def test_active_command_registry_is_bounded_and_terminal_entries_do_not_leak() -> None:
    service, _ = _service()
    for index in range(128):
        service.emit_lifecycle(_intent(str(index), "websocket", "ws-a"), "accepted")
    service.emit_lifecycle(_intent("0", "websocket", "ws-a"), "acknowledged")

    def refill(event: object) -> None:
        if getattr(event, "message", None) == "active command capacity exceeded":
            service.emit_lifecycle(_intent("cb", "websocket", "ws-a"), "accepted")

    service.subscribe_lifecycle(refill)  # type: ignore[arg-type]
    service.emit_lifecycle(_intent("128", "websocket", "ws-a"), "accepted")
    assert len(service._active_commands) == 128  # noqa: SLF001
    evicted = [event for event in service.lifecycle_events() if event.message]
    assert [event.command_id for event in evicted] == ["0", "1"]
    service.terminate_active_commands("shutdown")
    assert service._active_commands == {}  # noqa: SLF001
