"""MOR-2161 Slice 1: one descriptor-backed command reaches every drain."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.rigctld_client.radio import (
    RigctldClientObservationPoller,
    RigctldClientRadio,
)
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.core.acquisition_scheduler import AcquisitionScheduler
from rigplane.core.exceptions import CommandError, CommandRejectedError
from rigplane.core.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.core.command_dispatch import DescriptorTxPolicy
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


def _icom_poller(radio: MagicMock) -> RadioPoller:
    server = WebServer(radio, WebConfig())
    poller = RadioPoller(
        radio, server.command_queue, state_store=server.command_state_store
    )
    poller._acquisition_scheduler = AcquisitionScheduler(  # noqa: SLF001
        profile=radio.profile.state_acquisition
    )
    return poller


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
@pytest.mark.parametrize("surface", ["http", "batch", "ws"])
@pytest.mark.parametrize(
    ("error", "supported", "http_status", "error_code", "batch_status"),
    [
        (None, False, 409, "unsupported_command", "failed_validation"),
        (
            CommandRejectedError("radio returned ?;"),
            True,
            409,
            "radio_nak",
            "failed_execution",
        ),
        (
            CommandError("backend failed"),
            True,
            500,
            "command_failed",
            "failed_execution",
        ),
        (
            RuntimeError("temporarily not supported by transport"),
            True,
            500,
            "command_failed",
            "failed_execution",
        ),
        (RigplaneTimeoutError("late"), True, 504, "command_timeout", "timed_out"),
    ],
)
async def test_structured_error_reaches_every_surface(
    surface: str,
    error: Exception | None,
    supported: bool,
    http_status: int,
    error_code: str,
    batch_status: str,
) -> None:
    radio = _radio(error=error, supported=supported)
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
    if server.command_queue.has_commands:
        await _drain_yaesu(server, radio)
    await request
    if surface == "ws":
        body = ws.messages[-1]
    else:
        assert writer.status == (200 if surface == "batch" else http_status)
        body = writer.body
        if surface == "batch":
            assert body["ok"] is False
            body = body["results"][0]  # type: ignore[index,assignment]
            assert body["status"] == batch_status
    assert body["ok"] is False
    assert body["error"] == error_code


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
            radio,
            "set_repeater_shift",
            {"direction": 3, "receiver": 1},
            source="http",
        )
        assert str(intent.target) == "receiver.1.operator_controls.repeater_shift"
        if drain == "icom":
            await _icom_poller(radio)._execute(intent)  # noqa: SLF001
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
        radio.set_repeater_shift.assert_awaited_once_with(direction=3, receiver=1)


@pytest.mark.parametrize(
    "policy",
    [
        cast(Any, "defer"),
        cast(Any, "block"),
        cast(Any, None),
    ],
)
def test_descriptor_admission_rejects_non_admitted_policy(
    monkeypatch: pytest.MonkeyPatch,
    policy: DescriptorTxPolicy,
) -> None:
    from rigplane.core import command_dispatch
    from rigplane.core.command_dispatch import prepare_command_intent

    descriptor = command_dispatch.command_descriptor("set_repeater_shift")
    assert descriptor is not None
    mutated = replace(descriptor, tx_policy=policy)
    monkeypatch.setattr(
        command_dispatch, "_COMMAND_DESCRIPTORS", {mutated.name: mutated}
    )

    radio = _radio()
    with pytest.raises(CommandError, match="is not admitted"):
        prepare_command_intent(radio, mutated.name, {"direction": 1}, source="http")
    radio.supports_command.assert_not_called()
    radio.set_repeater_shift.assert_not_awaited()


@pytest.mark.asyncio
async def test_descriptor_enqueue_rejects_non_admitted_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.core import command_dispatch
    from rigplane.core.command_dispatch import (
        enqueue_command_intent,
        prepare_command_intent,
    )

    radio = _radio()
    intent = prepare_command_intent(
        radio, "set_repeater_shift", {"direction": 3}, source="http"
    )
    descriptor = command_dispatch.command_descriptor(intent.name)
    assert descriptor is not None
    mutated = replace(descriptor, tx_policy=cast(Any, "defer"))
    monkeypatch.setattr(
        command_dispatch, "_COMMAND_DESCRIPTORS", {mutated.name: mutated}
    )
    queue = MagicMock()
    future = asyncio.get_running_loop().create_future()

    with pytest.raises(CommandError, match="is not admitted"):
        enqueue_command_intent(
            queue,
            intent,
            future=future,
            command_id=intent.id,
            source=intent.source,
            session_id=None,
            command_service=object(),
            timeout=intent.timeout,
        )
    queue.put_ordered.assert_not_called()
    radio.set_repeater_shift.assert_not_awaited()


def _install_non_admitted_descriptor(monkeypatch: pytest.MonkeyPatch) -> None:
    from rigplane.core import command_dispatch

    descriptor = command_dispatch.command_descriptor("set_repeater_shift")
    assert descriptor is not None
    mutated = replace(descriptor, tx_policy=cast(Any, "block"))
    monkeypatch.setattr(
        command_dispatch, "_COMMAND_DESCRIPTORS", {mutated.name: mutated}
    )


@pytest.mark.asyncio
async def test_icom_drain_rejects_non_admitted_policy_before_radio_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    intent = prepare_command_intent(
        radio, "set_repeater_shift", {"direction": 3}, source="http"
    )
    _install_non_admitted_descriptor(monkeypatch)
    poller = _icom_poller(radio)

    with pytest.raises(CommandError, match="is not admitted"):
        await poller._execute(intent)  # noqa: SLF001
    radio.set_repeater_shift.assert_not_awaited()


@pytest.mark.asyncio
async def test_yaesu_drain_rejects_non_admitted_policy_before_radio_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    intent = prepare_command_intent(
        radio, "set_repeater_shift", {"direction": 3}, source="http"
    )
    _install_non_admitted_descriptor(monkeypatch)
    poller = YaesuCatPoller.__new__(YaesuCatPoller)
    poller._radio = radio  # type: ignore[attr-defined]

    with pytest.raises(CommandError, match="is not admitted"):
        await poller._execute_command(intent)  # noqa: SLF001
    radio.set_repeater_shift.assert_not_awaited()


@pytest.mark.asyncio
async def test_rigctld_drain_rejects_non_admitted_policy_before_radio_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    intent = prepare_command_intent(
        radio, "set_repeater_shift", {"direction": 3}, source="http"
    )
    _install_non_admitted_descriptor(monkeypatch)
    poller = RigctldClientObservationPoller.__new__(RigctldClientObservationPoller)
    poller._radio = radio  # type: ignore[attr-defined]

    with pytest.raises(CommandError, match="is not admitted"):
        await poller._execute_command(intent)  # noqa: SLF001
    radio.set_repeater_shift.assert_not_awaited()


@pytest.mark.asyncio
async def test_descriptor_enqueue_preserves_queue_lifecycle_metadata() -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    server = WebServer(radio, WebConfig())
    intent = prepare_command_intent(
        radio,
        "set_repeater_shift",
        {"direction": 2},
        source="websocket",
        command_id="metadata-shift",
        session_id="ws-metadata",
    )
    execution = asyncio.create_task(server.command_service.execute(intent))
    await asyncio.sleep(0)

    entries = server.command_queue.drain_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.command is intent
    assert entry.command_id == intent.id
    assert entry.source == intent.source
    assert entry.session_id == "ws-metadata"
    assert entry.command_service is server.command_service
    assert entry.future is not None
    assert entry.command.timeout == intent.timeout == 10.0

    entry.future.set_result(None)
    await execution
    terminal = [
        event.state
        for event in server.command_service.lifecycle_events()
        if event.command_id == intent.id
        and event.state in {"acknowledged", "failed", "timed_out"}
    ]
    assert terminal == ["acknowledged"]


@pytest.mark.asyncio
async def test_descriptor_queue_failure_completes_lifecycle_exactly_once() -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    server = WebServer(radio, WebConfig())
    intent = prepare_command_intent(
        radio,
        "set_repeater_shift",
        {"direction": 2},
        source="websocket",
        command_id="failed-shift",
        session_id="ws-failure",
    )
    execution = asyncio.create_task(server.command_service.execute(intent))
    await asyncio.sleep(0)
    (entry,) = server.command_queue.drain_entries()

    error = CommandError("radio write failed")
    YaesuCatPoller._mark_queued_command_failed(entry, error)  # noqa: SLF001
    assert entry.future is not None
    entry.future.set_exception(error)
    with pytest.raises(CommandError, match="radio write failed"):
        await execution

    terminal = [
        event.state
        for event in server.command_service.lifecycle_events()
        if event.command_id == intent.id
        and event.state in {"acknowledged", "failed", "timed_out"}
    ]
    assert terminal == ["failed"]


@pytest.mark.asyncio
async def test_descriptor_timeout_and_cancellation_cleanup_is_exact_once() -> None:
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    intent = prepare_command_intent(
        radio,
        "set_repeater_shift",
        {"direction": 1},
        source="websocket",
        command_id="cancel-me",
        session_id="ws-a",
    )
    server = WebServer(radio, WebConfig())
    service = server.command_service
    task = asyncio.create_task(service.execute(intent))
    await asyncio.sleep(0)
    assert ("websocket", "ws-a", "cancel-me") in service._active_commands  # noqa: SLF001
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (
        service.pending_overlays(
            source="websocket", session_id="ws-a", command_id="cancel-me"
        )
        == ()
    )
    assert ("websocket", "ws-a", "cancel-me") not in service._active_commands  # noqa: SLF001
    assert [
        event.state
        for event in service.lifecycle_events()
        if event.command_id == "cancel-me"
        and event.state in {"acknowledged", "failed", "timed_out"}
    ] == ["failed"]
    assert (
        service.terminate_active_commands("late", source="websocket", session_id="ws-a")
        == 0
    )
    await _drain_yaesu(server, radio)
    assert [
        event.state
        for event in service.lifecycle_events()
        if event.command_id == "cancel-me"
        and event.state in {"acknowledged", "failed", "timed_out"}
    ] == ["failed"]
    radio.set_repeater_shift.assert_not_awaited()

    timed = replace(intent, id="time-me", timeout=0.001)
    timed_service = server.command_service
    with pytest.raises(TimeoutError):
        await timed_service.execute(timed)
    assert [
        event.state
        for event in timed_service.lifecycle_events()
        if event.command_id == "time-me"
        and event.state in {"acknowledged", "failed", "timed_out"}
    ] == ["timed_out"]
    assert (
        timed_service.pending_overlays(
            source="websocket", session_id="ws-a", command_id="time-me"
        )
        == ()
    )
    assert ("websocket", "ws-a", "time-me") not in service._active_commands  # noqa: SLF001
    await _drain_yaesu(server, radio)
    assert [
        event.state
        for event in timed_service.lifecycle_events()
        if event.command_id == "time-me"
        and event.state in {"acknowledged", "failed", "timed_out"}
    ] == ["timed_out"]
    radio.set_repeater_shift.assert_not_awaited()


@pytest.mark.asyncio
async def test_descriptor_queue_policy_controls_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.core import command_dispatch
    from rigplane.core.command_dispatch import prepare_command_intent

    radio = _radio()
    descriptor = command_dispatch.command_descriptor("set_repeater_shift")
    assert descriptor is not None
    mutated = replace(descriptor, queue_policy=cast(Any, "unknown"))
    monkeypatch.setattr(
        command_dispatch,
        "_COMMAND_DESCRIPTORS",
        {mutated.name: mutated},
    )
    server = WebServer(radio, WebConfig())
    intent = prepare_command_intent(
        radio, mutated.name, {"direction": 1}, source="http"
    )

    with pytest.raises(CommandError, match="unsupported queue policy 'unknown'"):
        await server.command_service.execute(intent)
    assert not server.command_queue.has_commands


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
    from dataclasses import MISSING, fields

    from rigplane.core.command_dispatch import CommandDescriptor, command_descriptors
    from rigplane import _poller_types
    from rigplane.runtime import _poller_types as runtime_types
    from rigplane.web import radio_poller

    assert set(command_descriptors()) == {
        "set_repeater_shift",
        "set_af_level",
        "set_rf_gain",
        "set_squelch",
        "set_att",
    }
    descriptor = command_descriptors()["set_repeater_shift"]
    assert descriptor.tx_policy is DescriptorTxPolicy.TX_SAFE
    policy_field = next(
        field for field in fields(CommandDescriptor) if field.name == "tx_policy"
    )
    assert policy_field.default is MISSING
    assert "set_repeater_shift" in ControlHandler._COMMANDS  # noqa: SLF001
    attenuator = command_descriptors()["set_att"]
    assert attenuator.name == "set_att"
    assert attenuator.method_name == "set_attenuator_level"
    assert attenuator.argument_names == ("db", "receiver")
    assert attenuator.queue_policy == "coalesced"
    assert attenuator.receiver_aware
    assert attenuator.public_names == ("set_att", "set_attenuator")
    assert command_descriptors()["set_squelch"].public_names == (
        "set_sql",
        "set_squelch",
    )
    assert "set_att" in ControlHandler._COMMANDS  # noqa: SLF001
    assert "set_attenuator" in ControlHandler._COMMANDS  # noqa: SLF001
    assert "set_attenuator_level" not in ControlHandler._COMMANDS  # noqa: SLF001
    assert "set_preamp" in ControlHandler._COMMANDS  # noqa: SLF001
    assert not hasattr(runtime_types, "SetRepeaterShift")
    assert not hasattr(_poller_types, "SetRepeaterShift")
    assert not hasattr(radio_poller, "SetRepeaterShift")


@pytest.mark.parametrize(
    ("name", "method_name", "target"),
    [
        ("set_antenna", "set_antenna_1", "global.slow_state.rx_antenna_1"),
        ("set_antenna_1", "set_antenna_1", "global.slow_state.rx_antenna_1"),
        ("set_antenna_2", "set_antenna_2", "global.slow_state.rx_antenna_2"),
        (
            "set_rx_antenna",
            "set_rx_antenna_ant1",
            "global.slow_state.rx_antenna_1",
        ),
        (
            "set_rx_antenna_ant1",
            "set_rx_antenna_ant1",
            "global.slow_state.rx_antenna_1",
        ),
        (
            "set_rx_antenna_ant2",
            "set_rx_antenna_ant2",
            "global.slow_state.rx_antenna_2",
        ),
    ],
)
def test_antenna_aliases_share_canonical_descriptor_policy(
    name: str, method_name: str, target: str
) -> None:
    from rigplane.core.command_dispatch import (
        bind_command_intent,
        command_descriptor,
    )

    descriptor = command_descriptor(name)
    assert descriptor is not None
    assert descriptor.tx_policy is DescriptorTxPolicy.ANTENNA_SWITCH

    command = bind_command_intent(name, {"on": True}, source="test")
    assert command.name == method_name
    assert command.params["on"] is True
    assert command.params["enabled"] is True
    assert str(command.target) == target


def test_civ_output_ant_is_descriptor_tx_safe() -> None:
    from rigplane.core.command_dispatch import (
        bind_command_intent,
        command_descriptor,
    )

    descriptor = command_descriptor("set_civ_output_ant")
    assert descriptor is not None
    assert descriptor.tx_policy is DescriptorTxPolicy.TX_SAFE
    command = bind_command_intent(
        "set_civ_output_ant", {"enabled": False}, source="test"
    )
    assert command.name == "set_civ_output_ant"
    assert command.params["on"] is False
    assert command.params["enabled"] is False


def test_descriptor_lookup_rejects_unrepresentable_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.core import command_dispatch

    descriptor = command_dispatch.command_descriptor("set_repeater_shift")
    assert descriptor is not None
    invalid = replace(descriptor, tx_policy=cast(Any, "antenna-ish"))
    monkeypatch.setattr(
        command_dispatch, "_COMMAND_DESCRIPTORS", {invalid.name: invalid}
    )

    with pytest.raises(CommandError, match="is not admitted"):
        command_dispatch.command_descriptor(invalid.name)
    with pytest.raises(CommandError, match="is not admitted"):
        command_dispatch.command_descriptors()


@pytest.mark.asyncio
async def test_managed_shared_leaf_admits_exactly_once_before_execution() -> None:
    from rigplane.core.command_dispatch import (
        bind_command_intent,
        execute_command_intent,
    )

    radio = MagicMock()
    radio.set_rx_antenna_ant1 = AsyncMock()
    managed = MagicMock()
    managed.admit_managed_write = AsyncMock(return_value=True)
    command = bind_command_intent("set_rx_antenna_ant1", {"on": True}, source="test")

    await execute_command_intent(radio, command, managed_tx_authority=managed)

    managed.admit_managed_write.assert_awaited_once_with(command)
    radio.set_rx_antenna_ant1.assert_awaited_once_with(enabled=True)


@pytest.mark.asyncio
async def test_managed_shared_leaf_refusal_is_immediate_and_not_deferred() -> None:
    from rigplane.core.command_dispatch import (
        bind_command_intent,
        execute_command_intent,
    )

    radio = MagicMock()
    radio.set_rx_antenna_ant2 = AsyncMock()
    managed = MagicMock()
    managed.admit_managed_write = AsyncMock(return_value=False)
    command = bind_command_intent(
        "set_rx_antenna_ant2", {"enabled": False}, source="test"
    )

    with pytest.raises(CommandError, match="transmit authority"):
        await execute_command_intent(radio, command, managed_tx_authority=managed)

    managed.admit_managed_write.assert_awaited_once_with(command)
    radio.set_rx_antenna_ant2.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmanaged_shared_leaf_remains_direct() -> None:
    from rigplane.core.command_dispatch import (
        bind_command_intent,
        execute_command_intent,
    )

    radio = MagicMock()
    radio.set_rx_antenna_ant1 = AsyncMock()
    command = bind_command_intent(
        "set_rx_antenna_ant1", {"enabled": True}, source="public_api"
    )

    await execute_command_intent(radio, command)

    radio.set_rx_antenna_ant1.assert_awaited_once_with(enabled=True)


def test_antenna_descriptor_preflight_preserves_profile_rejection() -> None:
    from rigplane.core.command_dispatch import (
        CommandUnsupportedError,
        prepare_command_intent,
    )

    radio = _radio(supported=False)
    radio.set_rx_antenna_ant1 = AsyncMock()

    with pytest.raises(CommandUnsupportedError, match="active profile"):
        prepare_command_intent(
            radio, "set_rx_antenna_ant1", {"on": True}, source="test"
        )

    radio.supports_command.assert_called_once_with("set_rx_antenna_ant1")
    radio.set_rx_antenna_ant1.assert_not_awaited()


@pytest.mark.parametrize(
    ("params", "expected_db"),
    [
        ({"db": 20, "level": 3, "value": 1, "receiver": 1}, 3),
        ({"level": 6, "value": 1}, 6),
        ({"db": 12, "value": 1}, 12),
        ({"value": 1}, 0),
        ({}, 0),
    ],
    ids=("level-wins", "level-only", "db-only", "value-is-not-an-alias", "default"),
)
def test_att_helper_uses_public_precedence_through_canonical_binding(
    params: dict[str, int], expected_db: int
) -> None:
    from rigplane.core.command_service import command_intent_from_request

    intent = command_intent_from_request("set_att", params, source="public_api")

    assert intent.name == "set_attenuator_level"
    assert intent.params["db"] == expected_db
    assert intent.params["att"] == expected_db
    assert intent.params["receiver"] == params.get("receiver", 0)
