"""Canonical receive-level dispatch through an inert FTX-1 Web/backend path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from rigplane.backends.yaesu_cat import poller as yaesu_poller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.command_service import command_intent_from_request
from rigplane.core.state_pipeline_contracts import CommandIntent, FieldPath
from rigplane.runtime._poller_types import (
    CommandQueue,
    SetAfLevel,
    SetRfGain,
    SetSquelch,
)
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.server import WebConfig, WebServer
from test_web_command_batch_http import _post_json


LEVELS = (
    ("set_af_level", "af_level", "AG"),
    ("set_rf_gain", "rf_gain", "RG"),
    ("set_squelch", "squelch", "SQ"),
)


@pytest.fixture
def path(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True
    radio.radio_state.active = "SUB"
    write = AsyncMock()
    monkeypatch.setattr(radio._transport, "write", write)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    handler = ControlHandler(
        None, radio, "test", radio.model, server=server, session_id="levels-session"
    )
    poller = radio.create_observation_poller(
        callback=lambda observations: None, command_queue=server.command_queue
    )
    shared = AsyncMock(wraps=yaesu_poller.execute_command_intent)
    monkeypatch.setattr(yaesu_poller, "execute_command_intent", shared)
    entries = []
    drain_entries = server.command_queue.drain_entries

    def capture_entries():
        drained = drain_entries()
        entries.extend(drained)
        return drained

    monkeypatch.setattr(server.command_queue, "drain_entries", capture_entries)
    return SimpleNamespace(
        radio=radio,
        server=server,
        handler=handler,
        poller=poller,
        write=write,
        shared=shared,
        entries=entries,
    )


async def _admit(path, name, level, receiver=0, command_id="level"):
    return await asyncio.wait_for(
        path.handler._enqueue_command(
            name, {"level": level, "receiver": receiver}, command_id=command_id
        ),
        timeout=1.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "field", "prefix"), LEVELS)
@pytest.mark.parametrize("receiver", [0, 1], ids=["MAIN", "SUB"])
async def test_web_levels_invoke_canonical_executor_and_literal_receiver(
    path, name, field, prefix, receiver
):
    before = path.server.command_state_store.snapshot().fields
    result = await _admit(path, name, 73, receiver)
    assert result == {"level": 73, "receiver": receiver}
    path.write.assert_not_awaited()
    assert path.server.command_queue.has_commands
    assert path.server.command_state_store.snapshot().fields == before

    await path.poller._drain_commands()
    await path.poller._drain_commands()
    path.write.assert_awaited_once_with(f"{prefix}{receiver}073;")
    path.shared.assert_awaited_once()
    radio, intent = path.shared.await_args.args
    assert radio is path.radio
    assert isinstance(intent, CommandIntent)
    assert intent.name == name
    assert intent.target == FieldPath.receiver(
        str(receiver), "operator_controls", field
    )
    assert intent.expected_observations == (intent.target,)
    assert intent.params[field] == 73
    [entry] = path.entries
    assert entry.command is intent
    assert entry.command_id == intent.id == "level"
    assert entry.source == intent.source == "websocket"
    assert entry.session_id == intent.params["session_id"] == "levels-session"
    assert entry.command_service is path.server.command_service
    assert entry.future is None
    assert path.server.command_state_store.snapshot().fields == before


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "field", "prefix"), LEVELS)
async def test_web_pending_main_and_sub_survive_same_target_replacement(
    path, name, field, prefix
):
    await _admit(path, name, 10, 0, "main-old")
    await _admit(path, name, 20, 1, "sub")
    await _admit(path, name, 30, 0, "main-latest")
    await path.poller._drain_commands()
    assert path.write.await_args_list == [
        call(f"{prefix}0030;"),
        call(f"{prefix}1020;"),
    ]
    assert [entry.command_id for entry in path.entries] == ["main-latest", "sub"]
    assert all(entry.future is None for entry in path.entries)
    assert path.shared.await_count == 2


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
    for intent in intents:
        queue.put(intent, command_id=intent.id, source=intent.source)
    assert queue.drain() == [intents[4], intents[1], intents[2], intents[3]]


@pytest.mark.asyncio
async def test_sql_alias_has_canonical_name_target_and_observation_scale(path):
    await _admit(path, "set_sql", 1, 0, "alias")
    [overlay] = path.server.command_service.pending_overlays(
        source="websocket", session_id="levels-session"
    )
    assert overlay.path == FieldPath.receiver("0", "operator_controls", "squelch")
    assert overlay.value == pytest.approx(1 / 255)
    await path.poller._drain_commands()
    path.write.assert_awaited_once_with("SQ0001;")
    path.shared.assert_awaited_once()
    intent = path.shared.await_args.args[1]
    assert intent.name == "set_squelch"
    assert intent.params["squelch"] == 1
    assert intent.expected_observations == (overlay.path,)


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [SetAfLevel, SetRfGain, SetSquelch])
async def test_legacy_level_envelope_uses_shared_invocation(path, envelope):
    await path.poller._execute_command(envelope(73, receiver=1))
    path.shared.assert_awaited_once()
    intent = path.shared.await_args.args[1]
    assert isinstance(intent, CommandIntent)
    assert intent.params["receiver"] == 1
    assert path.write.await_count == 1


@pytest.mark.asyncio
async def test_batch_validation_is_capture_only(path):
    store = path.server.command_state_store
    service = path.server.command_service
    before = store.snapshot().fields
    events = service.lifecycle_events()
    for index, (name, _, _) in enumerate(LEVELS):
        step = await path.server._prepare_http_batch_step(
            index,
            {
                "id": f"batch-{index}",
                "name": name,
                "params": {"level": 73, "receiver": 1},
            },
        )
        assert not path.server.command_queue.has_commands
        path.write.assert_not_awaited()
        assert store.snapshot().fields == before
        assert service.lifecycle_events() == events
        assert service.pending_overlays(source="http", session_id=None) == ()
        assert isinstance(step.command, CommandIntent)
        assert step.command_id == f"batch-{index}"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_write", [False, True], ids=["complete", "failure"])
async def test_batch_waits_for_backend_and_executes_once(path, fail_write):
    entered = asyncio.Event()
    release = asyncio.Event()
    before = path.server.command_state_store.snapshot().fields

    async def held_write(frame):
        entered.set()
        await release.wait()
        if fail_write:
            raise RuntimeError("inert transport rejected write")

    path.write.side_effect = held_write
    request = asyncio.create_task(
        _post_json(
            path.server,
            "/api/v1/commands/batch",
            {
                "steps": [
                    {
                        "id": "batch-level",
                        "name": "set_rf_gain",
                        "params": {"level": 73, "receiver": 1},
                    }
                ]
            },
        )
    )
    drain = None
    try:
        await path.server.command_queue.wait(timeout=1.0)
        assert path.server.command_queue.has_commands
        drain = asyncio.create_task(path.poller._drain_commands())
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        assert not request.done()
        assert path.server.command_state_store.snapshot().fields == before
        [entry] = path.entries
        assert entry.future is not None and not entry.future.done()
        release.set()
        await asyncio.wait_for(drain, timeout=1.0)
        writer = await asyncio.wait_for(request, timeout=1.0)
        [result] = writer.response_body["results"]
        assert result["ok"] is not fail_write
        await path.poller._drain_commands()
        path.write.assert_awaited_once_with("RG1073;")
        path.shared.assert_awaited_once()
        assert entry.command_id == "batch-level"
        assert entry.source == "http" and entry.session_id is None
        assert entry.command_service is path.server.command_service
        accepted = [
            event
            for event in path.server.command_service.lifecycle_events()
            if event.state == "accepted"
        ]
        assert [event.command_id for event in accepted] == ["batch-level"]
        assert path.server.command_state_store.snapshot().fields == before
    finally:
        release.set()
        for task in (request, drain):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (request, drain) if task is not None),
            return_exceptions=True,
        )
