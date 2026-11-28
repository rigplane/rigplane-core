"""Additive refusal wire goldens replayed through the real ingress stack.

The fixture is shared with the frontend consumer test, like the host-api-v3
wire golden. Regressions before the fix: these routes omit ``code`` entirely.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_managed_tx_http_route import _Authority, _reader, _server, _Writer
from test_web_managed_ptt_rejection import (
    _drain_one,
    _gate_enqueue_after_failure,
    _key,
    _QueueStack,
    _release,
    _wait_barrier,
)

from rigplane.core.exceptions import CommandError
from rigplane.diagnostics._logging import _DIAGNOSTIC_FORMATTER
from rigplane.runtime.controller_authority import ControllerAuthority, ControllerError
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.web.protocol import decode_json

WIRE = json.loads(
    (Path(__file__).parent / "fixtures/managed-tx-refusal-wire.json").read_text()
)


def test_closed_producer_vocabulary_includes_reserved_radio_refusal() -> None:
    from rigplane.runtime.managed_tx_state import ManagedTxRefusalReason

    assert WIRE["codes"] == [reason.value for reason in ManagedTxRefusalReason]
    assert {reason.value for reason in ManagedTxRefusalReason} == {
        "tx_held_by_other",
        "release_in_progress",
        "authority_unavailable",
        "read_only",
        "controller_refused",
        "radio_refused",
        "other",
    }


async def test_http_controller_gate_adds_code_before_route_dispatch() -> None:
    server = _server(_Authority())
    reader, headers = _reader({"operation": "transmit_on"})
    headers["x-rigplane-controller"] = "invalid"
    writer = _Writer()
    await server._handle_http(
        writer,
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )
    assert writer.status == 403
    assert writer.payload == {
        "error": "controller_invalid",
        "code": "controller_refused",
    }


@pytest.mark.parametrize("surface", ("http", "ws"))
@pytest.mark.parametrize("releasing", (False, True), ids=("held", "releasing"))
async def test_refusal_goldens_from_real_queue_and_authority(
    surface: str,
    releasing: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stack = _QueueStack()
    stack.server._production_managed_tx_port = SimpleNamespace(
        authority=stack.authority
    )
    gate = None
    release = None
    try:
        await _key(stack, stack.handler("a"))
        if releasing:
            gate = stack.lane.block_next()
            release = await stack.authority.submit_ptt(False, "websocket-a")
        stack.enqueued.clear()
        writer = _Writer()
        ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
        if surface == "http":
            reader, headers = _reader({"operation": "transmit_on"})
            task = asyncio.create_task(
                stack.server._handle_http(
                    writer,
                    "POST",
                    "/api/v1/managed-transmit/command",
                    headers=headers,
                    reader=reader,
                )
            )
        else:
            task = asyncio.create_task(
                stack.handler("b", ws=ws)._dispatch_command(
                    "refusal-1",
                    "ptt_on",
                    {},
                )
            )
        await _wait_barrier(stack.enqueued)
        await _drain_one(stack)
        await task
        body = (
            writer.payload
            if surface == "http"
            else decode_json(ws.send_text.await_args.args[0])
        )
        assert body == WIRE[surface + ("Releasing" if releasing else "Held")]
        if surface == "ws":
            records = [
                record
                for record in caplog.records
                if record.name == "rigplane.web.handlers.control"
                and record.getMessage().startswith("control: command 'ptt_on' failed:")
            ]
            assert len(records) == 1
            line = _DIAGNOSTIC_FORMATTER.format(records[0])
            assert len(line.splitlines()) == 1
            assert line.endswith(
                f"control: command 'ptt_on' failed: {body['message']} code={body['code']}"
            )
        if surface == "http":
            assert writer.status == 409
        if gate is not None:
            assert not gate.is_set()
    finally:
        if gate is not None:
            gate.set()
        if release is not None:
            await release.wait_settlement()
        await _release(stack.authority)


@pytest.mark.parametrize("surface", ("http", "ws"))
async def test_refusal_code_uses_receipt_even_after_holder_releases(
    surface: str,
) -> None:
    stack = _QueueStack()
    stack.server._production_managed_tx_port = SimpleNamespace(
        authority=stack.authority
    )
    try:
        await _key(stack, stack.handler("a"))
        parked, gate = _gate_enqueue_after_failure(stack.server)
        stack.enqueued.clear()
        writer = _Writer()
        ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
        reader, headers = _reader({"operation": "transmit_on"})
        task = asyncio.create_task(
            stack.server._handle_http(
                writer,
                "POST",
                "/api/v1/managed-transmit/command",
                headers=headers,
                reader=reader,
            )
            if surface == "http"
            else stack.handler("b", ws=ws)._dispatch_command("refusal-1", "ptt_on", {})
        )
        await _wait_barrier(stack.enqueued)
        await _drain_one(stack)
        await _wait_barrier(parked)
        await stack.authority.ptt_up("websocket-a")
        gate.set()
        await task
        body = (
            writer.payload
            if surface == "http"
            else decode_json(ws.send_text.await_args.args[0])
        )
        assert body == WIRE[surface + "Held"]
    finally:
        await _release(stack.authority)


@pytest.mark.parametrize(
    ("case", "status"),
    (("ReadOnly", 403), ("Unavailable", 503), ("Controller", 403), ("Other", 409)),
)
async def test_http_refusal_goldens_keep_existing_fields(
    case: str, status: int
) -> None:
    authority = _Authority()
    authority.next_outcome = ManagedTxOutcome.REJECTED
    server = _server(
        None if case == "Unavailable" else authority, read_only=case == "ReadOnly"
    )
    if case == "Controller":
        server._controller = ControllerAuthority(
            ready=lambda: True, cleanup=AsyncMock()
        )
        await server._controller.set_mode("remote")
    reader, headers = _reader({"operation": "transmit_on"})
    writer = _Writer()
    await server._handle_http(
        writer,
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )
    assert writer.status == status
    assert writer.payload == WIRE["http" + case]


@pytest.mark.parametrize(
    ("error", "code"),
    (
        (ControllerError("controller_invalid"), "controller_refused"),
        (ControllerError("radio_tx_unowned", 409), "other"),
        (CommandError("positive TX queue entry was cancelled"), "other"),
    ),
)
async def test_http_exception_refusals_name_only_proven_reasons(
    error: Exception, code: str
) -> None:
    server = _server(_Authority())

    async def fail(**kwargs: object) -> None:
        pending = kwargs["submission"]
        assert isinstance(pending, asyncio.Task)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        raise error

    server.enqueue_managed_positive_tx = fail
    reader, headers = _reader({"operation": "transmit_on"})
    writer = _Writer()
    await server._handle_http(
        writer,
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )
    assert writer.payload["code"] == code


async def test_ws_missing_authority_has_typed_reason() -> None:
    stack = _QueueStack()
    ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
    handler = stack.handler("b", ws=ws)
    handler._managed_tx_authority = None
    try:
        await handler._dispatch_command("refusal-1", "ptt_on", {})
        assert decode_json(ws.send_text.await_args.args[0]) == WIRE["wsUnavailable"]
    finally:
        await _release(stack.authority)


@pytest.mark.parametrize("read_only", (False, True), ids=("controller", "read-only"))
async def test_ws_permission_refusals_carry_code(read_only: bool) -> None:
    stack = _QueueStack()
    ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
    handler = stack.handler("b", ws=ws)
    handler._read_only = read_only
    if not read_only:
        controller = ControllerAuthority(ready=lambda: True, cleanup=AsyncMock())
        await controller.set_mode("remote")
        stack.radio._controller_authority = controller
    try:
        await handler._dispatch_command("refusal-1", "ptt_on", {})
        body = decode_json(ws.send_text.await_args.args[0])
        assert body["code"] == ("read_only" if read_only else "controller_refused")
        assert body["ok"] is False
    finally:
        await _release(stack.authority)
