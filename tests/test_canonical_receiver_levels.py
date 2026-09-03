"""Canonical receive-level dispatch through inert production backend paths."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from rigplane.backends.rigctld_client import radio as rigctld_radio
from rigplane.backends.yaesu_cat import poller as yaesu_poller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
)
from rigplane.core.command_dispatch import CommandUnsupportedError
from rigplane.core.command_service import command_intent_from_request
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import CommandIntent, FieldPath
from rigplane.runtime._poller_types import PttOff, SetAfLevel, SetRfGain, SetSquelch
from rigplane.runtime.radio import CoreRadio
from rigplane.web import radio_poller as icom_poller
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.websocket import WS_OP_TEXT
from test_web_command_batch_http import _post_json


LEVELS = (
    ("set_af_level", "af_level", "AG"),
    ("set_rf_gain", "rf_gain", "RG"),
    ("set_squelch", "squelch", "SQ"),
)


def _path(monkeypatch, backend="yaesu", config="ftx1"):
    write = AsyncMock()
    if backend == "icom":
        radio = CoreRadio("127.0.0.1", model="IC-7300")
        radio._connected, radio._civ_transport = True, object()
        monkeypatch.setattr(radio, "_send_civ_raw", write)
        module = icom_poller
    elif backend == "rigctld":
        radio = rigctld_radio.RigctldClientRadio(host="127.0.0.1")
        monkeypatch.setattr(radio._transport, "command", write)
        module = rigctld_radio
    else:
        radio = YaesuCatRadio("/dev/null", profile=config, audio_driver=MagicMock())
        radio._transport._connected = True
        radio.radio_state.active = "SUB"
        monkeypatch.setattr(radio._transport, "write", write)
        module = yaesu_poller
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    handler = ControlHandler(
        None, radio, "test", radio.model, server=server, session_id="levels-session"
    )
    poller = (
        icom_poller.RadioPoller(
            radio, server.command_queue, state_store=server.command_state_store
        )
        if backend == "icom"
        else radio.create_observation_poller(
            callback=lambda observations: None, command_queue=server.command_queue
        )
    )
    shared = AsyncMock(wraps=module.execute_command_intent)
    monkeypatch.setattr(module, "execute_command_intent", shared)
    entries = []
    drain_entries = server.command_queue.drain_entries

    def capture_entries():
        drained = drain_entries()
        entries.extend(drained)
        return drained

    monkeypatch.setattr(server.command_queue, "drain_entries", capture_entries)

    async def drain():
        if backend == "icom":
            for entry in server.command_queue.drain_entries():
                await poller._execute_queued_entry(entry)
        else:
            await poller._drain_commands()

    return SimpleNamespace(
        radio=radio,
        server=server,
        handler=handler,
        poller=poller,
        write=write,
        shared=shared,
        entries=entries,
        backend=backend,
        drain=drain,
        execute=poller._execute if backend == "icom" else poller._execute_command,
    )


@pytest.fixture
def path(monkeypatch, request):
    result = _path(monkeypatch, getattr(request, "param", "yaesu"))
    yield result
    if result.backend == "icom":
        result.radio._connected = False


def _write_call(path, prefix, receiver):
    if path.backend == "icom":
        sub = {"AG": 1, "RG": 2, "SQ": 3}[prefix]
        frame = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x14, sub, 0, 0x73, 0xFD])
        return call(frame, wait_response=False)
    if path.backend == "rigctld":
        return call(f"L {'AF' if prefix == 'AG' else 'RF'} 0.286")
    return call(f"{prefix}{receiver}073;")


async def _admit(path, name, level, receiver=0, command_id="level"):
    return await asyncio.wait_for(
        path.handler._enqueue_command(
            name, {"level": level, "receiver": receiver}, command_id=command_id
        ),
        timeout=1.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "field", "prefix"), LEVELS)
@pytest.mark.parametrize(
    ("path", "receiver"),
    [("yaesu", 0), ("yaesu", 1), ("icom", 0)],
    indirect=["path"],
    ids=["MAIN", "SUB", "IC7300"],
)
async def test_web_levels_invoke_canonical_executor_and_literal_receiver(
    path, name, field, prefix, receiver
):
    before = path.server.command_state_store.snapshot().fields
    result = await _admit(path, name, 73, receiver)
    assert result == {"level": 73, "receiver": receiver}
    path.write.assert_not_awaited()
    assert path.server.command_queue.has_commands
    assert path.server.command_state_store.snapshot().fields == before

    await path.drain()
    await path.drain()
    assert path.write.await_args_list == [_write_call(path, prefix, receiver)]
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
    await path.drain()
    assert path.write.await_args_list == [
        call(f"{prefix}0030;"),
        call(f"{prefix}1020;"),
    ]
    assert [entry.command_id for entry in path.entries] == ["main-latest", "sub"]
    assert all(entry.future is None for entry in path.entries)
    assert path.shared.await_count == 2


@pytest.mark.asyncio
async def test_sql_alias_has_canonical_name_target_and_observation_scale(path):
    await _admit(path, "set_sql", 1, 0, "alias")
    [overlay] = path.server.command_service.pending_overlays(
        source="websocket", session_id="levels-session"
    )
    assert overlay.path == FieldPath.receiver("0", "operator_controls", "squelch")
    assert overlay.value == pytest.approx(1 / 255)
    await path.drain()
    path.write.assert_awaited_once_with("SQ0001;")
    path.shared.assert_awaited_once()
    intent = path.shared.await_args.args[1]
    assert intent.name == "set_squelch"
    assert intent.params["squelch"] == 1
    assert intent.expected_observations == (overlay.path,)


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [SetAfLevel, SetRfGain, SetSquelch])
@pytest.mark.parametrize("path", ["yaesu", "icom", "rigctld"], indirect=True)
async def test_legacy_level_envelope_uses_shared_invocation(path, envelope):
    if path.backend == "rigctld" and envelope is SetSquelch:
        with pytest.raises(CommandError):
            await path.execute(envelope(73))
        path.write.assert_not_awaited()
        return
    receiver = int(path.backend == "yaesu")
    await path.execute(envelope(73, receiver=receiver))
    path.shared.assert_awaited_once()
    intent = path.shared.await_args.args[1]
    assert isinstance(intent, CommandIntent)
    assert intent.params["receiver"] == receiver
    prefix = {SetAfLevel: "AG", SetRfGain: "RG", SetSquelch: "SQ"}[envelope]
    assert path.write.await_args_list == [_write_call(path, prefix, receiver)]


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
        drain = asyncio.create_task(path.drain())
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
        await path.drain()
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


@pytest.mark.parametrize("path", ["icom", "yaesu"], indirect=True)
@pytest.mark.parametrize(("name", "field", "prefix"), LEVELS)
async def test_unsupported_sub_refused_before_admission(
    path, monkeypatch, name, field, prefix
):
    if path.backend == "yaesu":
        config = path.radio._config
        commands = {
            key: value
            for key, value in config.commands.items()
            if key != f"{name}_sub"
        }
        path = _path(monkeypatch, config=replace(config, commands=commands))
    await _admit(path, name, 73)
    await path.drain()
    assert path.write.await_args_list == [_write_call(path, prefix, 0)]
    events = path.server.command_service.lifecycle_events()
    with pytest.raises(CommandUnsupportedError):
        await _admit(path, name, 73, 1, "missing-sub")
    assert not path.server.command_queue.has_commands
    assert path.server.command_service.lifecycle_events() == events
    assert (
        path.server.command_service.pending_overlays(
            source="websocket", session_id="levels-session", command_id="missing-sub"
        )
        == ()
    )


@pytest.mark.parametrize("path", ["icom"], indirect=True)
@pytest.mark.parametrize(("name", "field", "prefix"), LEVELS)
@pytest.mark.parametrize("fail_write", [False, True], ids=["complete", "failure"])
async def test_canonical_readback_waits_for_successful_write(
    path, name, field, prefix, fail_write
):
    scheduler = AcquisitionScheduler(profile=path.radio.profile.state_acquisition)
    path.poller._acquisition_scheduler = scheduler
    target = FieldPath.receiver("main", "operator_controls", field)
    scheduler.ensure_fresh(
        (target,),
        max_age=1.5,
        priority=AcquisitionPriority.BACKGROUND,
        reason="cadence",
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_write(*args, **kwargs):
        entered.set()
        await release.wait()
        if fail_write:
            raise RuntimeError("inert write failed")

    path.write.side_effect = held_write
    intent = command_intent_from_request(name, {"level": 73}, source="websocket")
    execution = asyncio.create_task(path.execute(intent))
    witness = asyncio.create_task(entered.wait())
    before = path.server.command_state_store.snapshot().fields
    try:
        done, _ = await asyncio.wait(
            (execution, witness), timeout=1.0, return_when=asyncio.FIRST_COMPLETED
        )
        if execution in done:
            await execution
        assert witness in done and not execution.done()
        assert (
            scheduler.pending_requests()[0].priority is AcquisitionPriority.BACKGROUND
        )
        release.set()
        if fail_write:
            with pytest.raises(RuntimeError, match="inert write failed"):
                await asyncio.wait_for(execution, timeout=1.0)
        else:
            await asyncio.wait_for(execution, timeout=1.0)
        [pending] = scheduler.pending_requests()
        assert pending.paths == (target,)
        assert pending.priority is (
            AcquisitionPriority.BACKGROUND if fail_write else AcquisitionPriority.USER
        )
        assert path.write.await_args_list == [_write_call(path, prefix, 0)]
        assert path.server.command_state_store.snapshot().fields == before
    finally:
        release.set()
        for task in (execution, witness):
            if not task.done():
                task.cancel()
        await asyncio.gather(execution, witness, return_exceptions=True)


async def test_control_run_admits_next_off_while_level_write_is_unsettled(path):
    incoming = asyncio.Queue()
    entered, release, off_ack = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def send_text(payload):
        message = json.loads(payload)
        if message.get("id") == "off" and message.get("ok") is True:
            off_ack.set()

    async def held_write(frame):
        entered.set()
        await release.wait()

    path.write.side_effect = held_write
    ws = SimpleNamespace(
        recv=AsyncMock(side_effect=incoming.get),
        send_text=AsyncMock(side_effect=send_text),
    )
    path.handler._ws = ws
    run = asyncio.create_task(path.handler.run())
    drain = None
    try:
        level = (
            b'{"type":"cmd","id":"level","name":"set_af_level",'
            b'"params":{"level":73}}'
        )
        await incoming.put((WS_OP_TEXT, level))
        await path.server.command_queue.wait(timeout=1.0)
        assert path.server.command_queue.has_commands
        drain = asyncio.create_task(path.drain())
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        off = b'{"type":"cmd","id":"off","name":"ptt_off","params":{}}'
        await incoming.put((WS_OP_TEXT, off))
        await asyncio.wait_for(off_ack.wait(), timeout=1.0)
        [entry] = path.server.command_queue.drain_entries()
        assert isinstance(entry.command, PttOff) and entry.command_id == "off"
        assert entry.source == "websocket" and entry.session_id == "levels-session"
        assert ws.recv.await_count >= 2 and not drain.done()
        path.write.assert_awaited_once_with("AG0073;")
    finally:
        release.set()
        run.cancel()
        if drain is not None and not drain.done():
            drain.cancel()
        await asyncio.gather(
            *(task for task in (run, drain) if task is not None),
            return_exceptions=True,
        )
