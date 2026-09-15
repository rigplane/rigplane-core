"""Executor isolation and semantic command queue identity."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    command_intent_from_request,
)
from rigplane.core.state_store import StateStore
from rigplane.runtime._poller_types import CommandQueue


def test_canonical_queue_keys_use_name_and_target_instead_of_intent_type():
    queue = CommandQueue()
    requests = [
        ("set_af_level", 0, 10),
        ("set_af_level", 1, 20),
        ("set_rf_gain", 0, 40),
        ("set_squelch", 0, 50),
        ("set_af_level", 0, 30),
    ]
    intents = [
        command_intent_from_request(
            name,
            {"level": level, "receiver": receiver},
            source="websocket",
            command_id=str(index),
            session_id="levels-session",
        )
        for index, (name, receiver, level) in enumerate(requests)
    ]
    intents.append(replace(intents[0], id="other", name="alternate_af_level"))
    for intent in intents:
        queue.put(intent, command_id=intent.id, source=intent.source)
    assert queue.drain() == [
        intents[4],
        intents[1],
        intents[2],
        intents[3],
        intents[5],
    ]


class _FalsyExecutor(SimpleNamespace):
    def __bool__(self) -> bool:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_override", [False, True], ids=["complete", "failure"])
async def test_per_call_executor_isolated_from_default_and_cleans_up(fail_override):
    store = StateStore()
    before = store.snapshot().fields
    default = SimpleNamespace(
        execute=AsyncMock(
            return_value=CommandExecutionResult(details={"executor": "default"})
        )
    )
    service = CommandService(executor=default, state_store=store)
    intent = command_intent_from_request(
        "set_rf_gain",
        {"level": 73},
        source="websocket",
        command_id="override",
        session_id="foundation",
    )
    overlap = replace(intent, id="default-overlap")
    follow_on = replace(intent, id="default-after")
    entered = asyncio.Event()
    release = asyncio.Event()
    error = RuntimeError("override rejected")

    async def held_override(received):
        entered.set()
        await release.wait()
        if fail_override:
            raise error
        return CommandExecutionResult(details={"executor": "override"})

    override = _FalsyExecutor(execute=AsyncMock(side_effect=held_override))
    task = asyncio.create_task(service.execute(intent, executor=override))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        assert service._executor is default
        assert service.pending_overlays(
            source="websocket", session_id="foundation", command_id=intent.id
        )
        result = await asyncio.wait_for(service.execute(overlap), timeout=1.0)
        assert result.executor_result.details == {"executor": "default"}
        assert not task.done()
        release.set()
        if fail_override:
            with pytest.raises(RuntimeError, match="override rejected") as caught:
                await asyncio.wait_for(task, timeout=1.0)
            assert caught.value is error
            assert (
                service.pending_overlays(
                    source="websocket", session_id="foundation", command_id=intent.id
                )
                == ()
            )
        else:
            result = await asyncio.wait_for(task, timeout=1.0)
            assert result.executor_result.details == {"executor": "override"}
        result = await asyncio.wait_for(service.execute(follow_on), timeout=1.0)
        assert result.executor_result.details == {"executor": "default"}
        assert service._executor is default
        default.execute.assert_has_awaits([call(overlap), call(follow_on)])
        assert default.execute.await_count == 2
        override.execute.assert_awaited_once_with(intent)
        for command in (intent, overlap, follow_on):
            terminal = (
                "failed" if command is intent and fail_override else "acknowledged"
            )
            assert [
                event.state
                for event in service.lifecycle_events()
                if event.command_id == command.id
            ] == ["accepted", "queued", "sent", terminal]
        expected_active = 2 if fail_override else 3
        assert service.terminate_active_commands("late cleanup") == expected_active
        assert service.terminate_active_commands("late cleanup") == 0
        assert store.snapshot().fields == before
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
