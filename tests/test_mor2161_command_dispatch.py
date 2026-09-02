"""MOR-2161 Slice 1: one descriptor-backed command reaches every drain."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.rigctld_client.radio import (
    RigctldClientObservationPoller,
    RigctldClientRadio,
)
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass

    @property
    def status(self) -> int:
        return int(self.buffer.split(b"\r\n", 1)[0].split()[1])

    @property
    def body(self) -> dict[str, object]:
        return json.loads(self.buffer.split(b"\r\n\r\n", 1)[1])


class _Ws:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


def _radio(
    *, error: Exception | None = None, model: str = "FTX-1", supported: bool = True
) -> MagicMock:
    profile = resolve_radio_profile(model=model)
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.supports_command = MagicMock(return_value=supported)
    radio.set_repeater_shift = AsyncMock(side_effect=error)
    return radio


async def _drain_yaesu(server: WebServer, radio: object) -> None:
    await server.command_queue.wait(timeout=1.0)
    poller = YaesuCatPoller.__new__(YaesuCatPoller)
    poller._radio = radio  # type: ignore[attr-defined]
    for entry in server.command_queue.drain_entries():
        if entry.future is not None and entry.future.cancelled():
            continue
        try:
            await poller._execute_command(entry.command)  # noqa: SLF001
        except BaseException as exc:
            if entry.future is not None and not entry.future.done():
                entry.future.set_exception(exc)
        else:
            if entry.future is not None and not entry.future.done():
                entry.future.set_result(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["http", "ws"])
async def test_success_waits_for_real_yaesu_drain(surface: str) -> None:
    radio = _radio()
    server = WebServer(radio, WebConfig())
    if surface == "http":
        writer = _Writer()
        request = asyncio.create_task(
            server._handle_http_single_command(  # noqa: SLF001
                writer,  # type: ignore[arg-type]
                {
                    "id": "shift",
                    "name": "set_repeater_shift",
                    "params": {"direction": 1},
                },
            )
        )
    else:
        ws = _Ws()
        handler = ControlHandler(ws, radio, "test", "FTX-1", server=server)  # type: ignore[arg-type]
        request = asyncio.create_task(
            handler._dispatch_command("shift", "set_repeater_shift", {"direction": 1})  # noqa: SLF001
        )

    await asyncio.sleep(0)
    assert not request.done()
    await _drain_yaesu(server, radio)
    await request
    radio.set_repeater_shift.assert_awaited_once_with(direction=1, receiver=0)
    radio.supports_command.assert_called_once_with("set_repeater_shift")
    with pytest.raises(KeyError):
        server.command_state_store.snapshot().field(
            "receiver.0.operator_controls.repeater_shift"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "error", "expected"),
    [
        ("http", CommandRejectedError("radio returned ?;"), (409, "radio_nak")),
        ("batch", CommandRejectedError("radio returned ?;"), (200, "radio_nak")),
        ("ws", CommandRejectedError("radio returned ?;"), (None, "radio_nak")),
        ("http", RigplaneTimeoutError("late"), (504, "command_timeout")),
        ("batch", RigplaneTimeoutError("late"), (200, "command_timeout")),
        ("ws", RigplaneTimeoutError("late"), (None, "command_timeout")),
    ],
)
async def test_structured_error_reaches_every_surface(
    surface: str, error: Exception, expected: tuple[int | None, str]
) -> None:
    radio = _radio(error=error)
    server = WebServer(radio, WebConfig())
    payload = {"id": "shift", "name": "set_repeater_shift", "params": {"direction": 2}}
    if surface == "http":
        writer = _Writer()
        request = asyncio.create_task(
            server._handle_http_single_command(writer, payload)  # type: ignore[arg-type] # noqa: SLF001
        )
    elif surface == "batch":
        writer = _Writer()
        request = asyncio.create_task(
            server._handle_http_command_batch(  # type: ignore[arg-type] # noqa: SLF001
                writer, {"steps": [payload]}
            )
        )
    else:
        ws = _Ws()
        handler = ControlHandler(ws, radio, "test", "FTX-1", server=server)  # type: ignore[arg-type]
        request = asyncio.create_task(
            handler._dispatch_command("shift", "set_repeater_shift", {"direction": 2})  # noqa: SLF001
        )

    await asyncio.sleep(0)
    await _drain_yaesu(server, radio)
    await request
    if surface == "ws":
        body = ws.messages[-1]
    else:
        assert writer.status == expected[0]
        body = writer.body
        if surface == "batch":
            body = body["results"][0]  # type: ignore[index,assignment]
    assert body["error"] == expected[1]


@pytest.mark.asyncio
async def test_descriptor_preflight_rejects_without_queue_or_overlay() -> None:
    from rigplane.core.command_dispatch import CommandUnsupportedError

    radios = (
        _radio(model="IC-7300", supported=False),
        RigctldClientRadio(host="127.0.0.1", port=4532),
    )
    for radio in radios:
        server = WebServer(radio, WebConfig())
        with pytest.raises(CommandUnsupportedError):
            await server._control_handler_for()._enqueue_command(  # noqa: SLF001
                "set_repeater_shift", {"direction": 1}, source="http"
            )
        assert not server.command_queue.has_commands
        assert server.command_service.lifecycle_events() == ()


@pytest.mark.asyncio
async def test_all_three_drains_execute_the_same_neutral_intent() -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    for drain in ("icom", "yaesu", "rigctld"):
        radio = _radio()
        intent = prepare_command_intent(
            radio, "set_repeater_shift", {"direction": 3}, source="http"
        )
        if drain == "icom":
            poller = SimpleNamespace(
                _radio=radio,
                _enforce_tx_interlock=lambda command: None,
                _provider_generation=lambda: 0,
            )
            await RadioPoller._execute(poller, intent)  # type: ignore[arg-type] # noqa: SLF001
        elif drain == "yaesu":
            poller = YaesuCatPoller.__new__(YaesuCatPoller)
            poller._radio = radio  # type: ignore[attr-defined]
            await poller._execute_command(intent)  # noqa: SLF001
        else:
            poller = RigctldClientObservationPoller.__new__(
                RigctldClientObservationPoller
            )
            poller._radio = radio  # type: ignore[attr-defined]
            await poller._execute_command(intent)  # noqa: SLF001
        radio.set_repeater_shift.assert_awaited_once_with(direction=3, receiver=0)


@pytest.mark.asyncio
async def test_descriptor_timeout_and_cancellation_cleanup_is_exact_once() -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    intent = prepare_command_intent(
        radio,
        "set_repeater_shift",
        {"direction": 1, "session_id": "ws-a"},
        source="websocket",
        command_id="cancel-me",
    )
    server = WebServer(radio, WebConfig())
    service = server.command_service
    task = asyncio.create_task(service.execute(intent))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (
        service.pending_overlays(
            source="websocket", session_id="ws-a", command_id="cancel-me"
        )
        == ()
    )
    assert [event.state for event in service.lifecycle_events()].count("failed") == 1
    assert service.terminate_active_commands("late") == 0
    await _drain_yaesu(server, radio)
    radio.set_repeater_shift.assert_not_awaited()

    timed = replace(intent, id="time-me", timeout=0.001)
    timed_service = server.command_service
    with pytest.raises(TimeoutError):
        await timed_service.execute(timed)
    assert [event.state for event in timed_service.lifecycle_events()].count(
        "timed_out"
    ) == 1
    assert (
        timed_service.pending_overlays(
            source="websocket", session_id="ws-a", command_id="time-me"
        )
        == ()
    )
    await _drain_yaesu(server, radio)
    radio.set_repeater_shift.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_preparation_is_capture_only_then_executes_once() -> None:
    radio = _radio()
    server = WebServer(radio, WebConfig())
    before = server.command_service.lifecycle_events()

    step = await server._prepare_http_batch_step(  # noqa: SLF001
        0, {"name": "set_repeater_shift", "params": {"direction": 1}}
    )

    assert server.command_service.lifecycle_events() == before
    assert not server.command_queue.has_commands
    assert isinstance(step.command, CommandIntent)
    radio.supports_command.assert_called_once_with("set_repeater_shift")
    execution = asyncio.create_task(server.command_service.execute(step.command))
    await asyncio.sleep(0)
    await _drain_yaesu(server, radio)
    result = await execution
    assert result.executor_result.details == {"direction": 1, "receiver": 0}
    radio.set_repeater_shift.assert_awaited_once()
    radio.supports_command.assert_called_once_with("set_repeater_shift")


def test_descriptor_is_the_only_migrated_name_source() -> None:
    from rigplane.core.command_dispatch import command_descriptors
    from rigplane import _poller_types
    from rigplane.runtime import _poller_types as runtime_types
    from rigplane.web import radio_poller

    assert set(command_descriptors()) == {"set_repeater_shift"}
    assert "set_repeater_shift" in ControlHandler._COMMANDS  # noqa: SLF001
    assert not hasattr(runtime_types, "SetRepeaterShift")
    assert not hasattr(_poller_types, "SetRepeaterShift")
    assert not hasattr(radio_poller, "SetRepeaterShift")
