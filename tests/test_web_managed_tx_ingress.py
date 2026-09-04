from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.command_dispatch import CommandUnsupportedError
from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.web import handlers as web_handlers
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import CommandQueue
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.transport.webrtc_session import WebRtcSessionManager
from test_managed_tx_http_route import _Writer, _reader


@dataclass
class _Submission:
    outcome: ManagedTxOutcome

    async def wait_settlement(self) -> None:
        raise AssertionError("Web PTT admission waited for provider settlement")


class _Authority:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []
        self.next_outcome = ManagedTxOutcome.ACCEPTED
        self.force_off_calls = 0
        self.disconnect_calls: list[str] = []
        self.intent: str = "rx"
        self.tot_deadline = 120.0
        self.disconnect_started = asyncio.Event()
        self.disconnect_gate: asyncio.Event | None = None

    def start_ptt_submission(
        self,
        on: bool,
        owner: str,
        *,
        ready: asyncio.Future[None],
        expires_at_monotonic: float,
    ) -> asyncio.Task[_Submission]:
        self.calls.append((on, owner))
        submission = _Submission(self.next_outcome)

        async def complete() -> _Submission:
            await ready
            if submission.outcome is ManagedTxOutcome.ACCEPTED:
                self.intent = f"ptt:{owner}" if on else "rx"
            return submission

        del expires_at_monotonic
        return asyncio.create_task(complete())

    async def submit_ptt(self, on: bool, owner: str) -> _Submission:
        self.calls.append((on, owner))
        submission = _Submission(self.next_outcome)
        if submission.outcome is ManagedTxOutcome.ACCEPTED:
            self.intent = f"ptt:{owner}" if on else "rx"
        return submission

    async def submit_force_off(self) -> _Submission:
        self.force_off_calls += 1
        return _Submission(self.next_outcome)

    async def owner_disconnect(self, owner: str) -> ManagedTxOutcome:
        self.disconnect_calls.append(owner)
        self.disconnect_started.set()
        if self.disconnect_gate is not None:
            await self.disconnect_gate.wait()
        outcome = (
            ManagedTxOutcome.ACCEPTED
            if self.intent == f"ptt:{owner}"
            else ManagedTxOutcome.REJECTED
        )
        if outcome is ManagedTxOutcome.ACCEPTED:
            self.intent = "rx"
        return outcome


def _radio() -> MagicMock:
    profile = resolve_radio_profile(model="IC-9700")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.connected = True
    radio.radio_ready = True
    radio.set_ptt = AsyncMock()
    return radio


def _state_store(observed: object = None, *, stale: bool = False) -> StateStore:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    if observed is not None:
        store.apply(
            Observation(
                path=FieldPath.global_("tx_state", "ptt"),
                value=observed,
                source=SourceMetadata(source="poll_response", provider="test"),
                timestamp_monotonic=clock.now(),
                max_age=1.0,
            )
        )
        if stale:
            store.mark_stale_due(now=12.0)
    return store


def _handler(
    authority: _Authority | None,
    *,
    read_only: bool = False,
    observed: object = None,
    stale: bool = False,
    ws: Any | None = None,
) -> tuple[ControlHandler, SimpleNamespace, MagicMock]:
    queue = MagicMock()
    retained: set[asyncio.Task[Any]] = set()

    def spawn(coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        retained.add(task)
        task.add_done_callback(retained.discard)
        return task

    async def enqueue_managed_positive_tx(
        *,
        ready: asyncio.Future[None],
        submission: asyncio.Task[_Submission],
        **_kwargs: Any,
    ) -> _Submission:
        queue.put_ordered(
            None, positive_tx_ready=ready, positive_tx_submission=submission
        )
        ready.set_result(None)
        return await submission

    server = SimpleNamespace(
        command_queue=queue,
        command_state_store=_state_store(observed, stale=stale),
        register_control_event_queue=MagicMock(return_value={}),
        unregister_control_event_queue=MagicMock(),
        build_state_update_envelope=MagicMock(return_value={}),
        _managed_tx_authority=lambda: authority,
        enqueue_managed_positive_tx=enqueue_managed_positive_tx,
        _spawn=spawn,
        retained=retained,
    )
    radio = _radio()
    handler = ControlHandler(
        ws=ws or MagicMock(),
        radio=radio,
        server_version="test",
        radio_model="IC-9700",
        server=server,
        read_only=read_only,
        session_id="websocket-test",
        managed_tx_authority=authority,
    )
    return handler, server, radio


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params", "on", "result"),
    (
        ("ptt", {"state": True}, True, {"state": True}),
        ("ptt", {"state": False}, False, {"state": False}),
        ("ptt_on", {}, True, {}),
        ("ptt_off", {}, False, {}),
    ),
)
async def test_ws_ptt_aliases_use_shared_queue_for_on_and_direct_authority_for_off(
    name: str, params: dict[str, Any], on: bool, result: dict[str, Any]
) -> None:
    authority = _Authority()
    handler, server, radio = _handler(authority)
    assert await handler._enqueue_command(name, params) == result
    assert authority.calls == [(on, "websocket-test")]
    assert authority.force_off_calls == 0
    server.command_queue.put.assert_not_called()
    if on:
        server.command_queue.put_ordered.assert_called_once()
    else:
        server.command_queue.put_ordered.assert_not_called()
    radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", (1, "false", {}), ids=("integer", "string", "object"))
async def test_ws_ptt_rejects_non_boolean_state_before_authority(state: object) -> None:
    authority = _Authority()
    handler, server, radio = _handler(authority)
    with pytest.raises(ValueError, match="state must be a boolean"):
        await handler._enqueue_command("ptt", {"state": state})
    assert authority.calls == []
    server.command_queue.put.assert_not_called()
    radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_admission_is_a_terminal_ws_error() -> None:
    authority = _Authority()
    authority.next_outcome = ManagedTxOutcome.REJECTED
    ws = SimpleNamespace(send_text=AsyncMock())
    handler, _, _ = _handler(authority, ws=ws)
    await handler._dispatch_command("command-1", "ptt_on", {})
    response = decode_json(ws.send_text.await_args.args[0])
    assert response["ok"] is False
    assert response["id"] == "command-1"
    assert authority.calls == [(True, "websocket-test")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observed", "stale"),
    ((False, False), (True, False), (None, False), (True, True)),
    ids=("rx", "tx", "unknown", "stale"),
)
async def test_observed_rf_never_changes_ptt_admission(
    observed: object, stale: bool
) -> None:
    authority = _Authority()
    handler, server, _ = _handler(authority, observed=observed, stale=stale)
    await handler._enqueue_command("ptt_on", {})
    assert authority.calls == [(True, "websocket-test")]
    server.command_queue.put.assert_not_called()


@pytest.mark.asyncio
async def test_missing_authority_and_ownerless_http_ptt_fail_closed() -> None:
    handler, server, radio = _handler(None)
    with pytest.raises(CommandRejectedError, match="authority unavailable"):
        await handler._enqueue_command("ptt_on", {})
    with pytest.raises(CommandUnsupportedError, match="WebSocket owner"):
        await handler._enqueue_command("ptt_off", {}, source="http")
    server.command_queue.put.assert_not_called()
    radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_rejection_fails_before_wire() -> None:
    authority = _Authority()
    authority.next_outcome = ManagedTxOutcome.REJECTED
    handler, server, radio = _handler(authority)
    with pytest.raises(CommandRejectedError):
        await handler._enqueue_command("ptt_on", {})
    server.command_queue.put.assert_not_called()
    radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeat_ptt_on_does_not_mutate_tot_or_wait_for_settlement() -> None:
    authority = _Authority()
    handler, _, _ = _handler(authority)
    await handler._enqueue_command("ptt_on", {})
    await handler._enqueue_command("ptt_on", {})
    assert authority.calls == [
        (True, "websocket-test"),
        (True, "websocket-test"),
    ]
    assert authority.tot_deadline == 120.0


def _eof_ws() -> SimpleNamespace:
    async def recv() -> tuple[int, bytes]:
        raise EOFError

    return SimpleNamespace(send_text=AsyncMock(), recv=recv)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "expected"),
    (
        ("ptt:websocket-test", "rx"),
        ("ptt:websocket-other", "ptt:websocket-other"),
        ("transmit", "transmit"),
    ),
    ids=("matching-owner", "unrelated-owner", "transmit-latch"),
)
async def test_disconnect_is_owner_scoped_and_preserves_transmit(
    intent: str, expected: str
) -> None:
    authority = _Authority()
    authority.intent = intent
    handler, server, _ = _handler(authority, ws=_eof_ws())
    await handler.run()
    assert authority.disconnect_calls == ["websocket-test"]
    assert authority.intent == expected
    assert authority.force_off_calls == 0
    server.command_queue.put.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_handler_finishes_cleanup_and_retains_disconnect_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _Authority()
    authority.intent = "ptt:websocket-test"
    authority.disconnect_gate = asyncio.Event()
    recv_gate = asyncio.Event()
    recv_started = asyncio.Event()
    sender_cancelled = asyncio.Event()

    async def recv() -> tuple[int, bytes]:
        recv_started.set()
        await recv_gate.wait()
        raise EOFError

    async def event_sender() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sender_cancelled.set()
            raise

    handler, server, _ = _handler(
        authority, ws=SimpleNamespace(send_text=AsyncMock(), recv=recv)
    )
    queue = CommandQueue()
    server.command_queue = queue
    server.unregister_control_event_queue.side_effect = RuntimeError(
        "unregister failed"
    )
    monkeypatch.setattr(handler, "_event_sender_loop", event_sender)
    pending_flush = asyncio.create_task(asyncio.Event().wait())
    handler._cmd_flush_tasks["pending"] = pending_flush  # noqa: SLF001
    handler._cmd_pending["pending"] = ("id", "set_freq", {})  # noqa: SLF001
    task = asyncio.create_task(handler.run())
    try:
        await recv_started.wait()
        assert queue.session_is_live("websocket-test")
        task.cancel()
        await authority.disconnect_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not queue.session_is_live("websocket-test")
        server.unregister_control_event_queue.assert_called_once_with(
            handler._event_queue,  # noqa: SLF001
            session_id="websocket-test",
        )
        assert sender_cancelled.is_set()
        assert not handler._cmd_flush_tasks  # noqa: SLF001
        assert not handler._cmd_pending  # noqa: SLF001
        assert pending_flush.cancelling()
        assert server.retained
        release_task = next(iter(server.retained))
        assert not release_task.cancelled()
        authority.disconnect_gate.set()
        await release_task
        assert authority.disconnect_calls == ["websocket-test"]
        assert authority.intent == "rx"
    finally:
        authority.disconnect_gate.set()
        task.cancel()
        pending_flush.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(asyncio.CancelledError):
            await pending_flush


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload", "status"),
    (
        ("/api/v1/commands", {"name": "ptt_on", "params": {}}, 409),
        (
            "/api/v1/commands/batch",
            {"steps": [{"name": "ptt_off", "params": {}}]},
            200,
        ),
    ),
    ids=("single", "batch"),
)
async def test_generic_http_ptt_is_rejected_before_queue(
    path: str, payload: dict[str, Any], status: int
) -> None:
    server = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    reader, headers = _reader(payload)
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        path,
        headers=headers,
        reader=reader,
    )

    assert writer.status == status
    assert writer.payload["ok"] is False
    assert not server.command_queue.drain()


@pytest.mark.asyncio
async def test_managed_force_off_remains_available_while_generic_ptt_is_closed() -> (
    None
):
    authority = _Authority()
    server = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0, read_only=True))
    server._production_managed_tx_port = SimpleNamespace(authority=authority)  # noqa: SLF001
    reader, headers = _reader({"operation": "force_off"})
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )

    assert writer.status == 202
    assert authority.force_off_calls == 1
    assert not server.command_queue.drain()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_server", (True, False), ids=("server", "serverless"))
async def test_webrtc_control_handler_receives_explicit_exact_authority(
    monkeypatch: pytest.MonkeyPatch, with_server: bool
) -> None:
    authority = _Authority()
    server = None
    if with_server:
        server = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
        server._production_managed_tx_port = SimpleNamespace(  # noqa: SLF001
            authority=authority
        )
    captured: dict[str, Any] = {}

    class _Handler:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(web_handlers, "ControlHandler", _Handler)
    manager = WebRtcSessionManager(_radio(), server, "IC-9700")
    session = SimpleNamespace(pc=MagicMock(), tasks=[])
    channel = SimpleNamespace(label="control", on=MagicMock())

    manager._dispatch_channel(session, channel)  # noqa: SLF001
    await session.tasks[0]

    assert captured.get("managed_tx_authority") is (authority if with_server else None)


def test_managed_web_ptt_cannot_reach_legacy_control_producers() -> None:
    run_source = inspect.getsource(ControlHandler.run)
    enqueue_source = inspect.getsource(ControlHandler._enqueue_command)

    assert "_release_ptt_on_teardown" not in run_source
    assert enqueue_source.index("_MANAGED_PTT_COMMANDS") < enqueue_source.index(
        "_command_service.execute"
    )


def test_state_projection_keeps_authority_separate_from_observed_rf() -> None:
    source = inspect.getsource(WebServer._managed_tx_document)

    assert "authority.snapshot" in source
    assert "project_observed_ptt" in source
    assert "observed_ptt" not in inspect.getsource(ControlHandler._enqueue_command)
