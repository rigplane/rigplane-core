"""MOR-3684: a refused managed PTT admission must say who holds the radio.

The real chain runs end to end: a real ``WebServer``
``enqueue_managed_positive_tx``, a real ``CommandQueue`` drained through the
real ``RadioPoller`` execution leg, and a real ``ManagedTxAuthority`` over
the fake effect lane — so every receipt, transition and state below is a
production dataclass. Holder-conflict wording is classified from THIS
receipt's immutable transition, never from live authority state.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_managed_tx_authority import FakeConfigStore, FakeFence, FakeLane

from rigplane.core.exceptions import CommandError, CommandRejectedError
from rigplane.core.radio_protocol import Radio
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.managed_tx_authority import (
    ManagedTxAuthority,
    ManagedTxSubmission,
)
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxState,
    ManagedTxTransition,
    ReleasePlan,
)
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.server import WebConfig, WebServer

_HOLDER_CONFLICT = "another client is transmitting"
_UNAVAILABLE = "managed transmit authority unavailable"
_ADMISSION_REJECTED = "managed PTT admission rejected"


class _LoopClock:
    """Authority clock on the loop base; ``bias`` drives the expiry test."""

    def __init__(self) -> None:
        self.bias = 0.0

    def __call__(self) -> float:
        return asyncio.get_running_loop().time() + self.bias


def _radio() -> MagicMock:
    profile = resolve_radio_profile(model="IC-9700")
    radio = MagicMock(spec=Radio)
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.connected = True
    radio.radio_ready = True
    radio.set_ptt = AsyncMock()
    radio._civ_epoch = 1
    return radio


class _Stack:
    def __init__(self) -> None:
        self.clock = _LoopClock()
        log: list[tuple[str, object] | tuple[str]] = []
        self.lane = FakeLane(log)
        self.authority = ManagedTxAuthority(
            self.lane,
            FakeConfigStore(10.0),
            FakeFence(log),
            provider_generation=7,
            clock=self.clock,
        )


class _QueueStack(_Stack):
    """The authority wired to a real WebServer and poller-drained queue."""

    def __init__(self) -> None:
        super().__init__()
        self.radio = _radio()
        self.server = WebServer(self.radio, WebConfig(host="127.0.0.1", port=0))
        self.queue: CommandQueue = self.server.command_queue
        self.poller = RadioPoller(
            self.radio,
            self.queue,
            state_store=self.server.command_state_store,
            managed_tx_authority=self.authority,
        )
        self.enqueued = asyncio.Event()
        put_ordered = self.queue.put_ordered

        def notify_put(*args: Any, **kwargs: Any) -> Any:
            entry = put_ordered(*args, **kwargs)
            self.enqueued.set()
            return entry

        self.queue.put_ordered = notify_put

    def handler(self, name: str, *, ws: Any = None) -> ControlHandler:
        return ControlHandler(
            ws or MagicMock(),
            self.radio,
            "test",
            "IC-9700",
            server=self.server,
            managed_tx_authority=self.authority,
            session_id=f"websocket-{name}",
        )


async def _wait_barrier(barrier: asyncio.Event) -> None:
    await asyncio.wait_for(barrier.wait(), timeout=5.0)


async def _drain_one(stack: _QueueStack) -> None:
    entry = stack.queue.take_entry()
    assert entry is not None
    with contextlib.suppress(Exception):
        await stack.poller._execute_queued_entry(entry)


async def _run_ptt_on(stack: _QueueStack, handler: ControlHandler) -> Any:
    stack.enqueued.clear()
    task = asyncio.create_task(handler._enqueue_command("ptt_on", {}))
    await _wait_barrier(stack.enqueued)
    await _drain_one(stack)
    return await task


async def _key(stack: _QueueStack, handler: ControlHandler) -> Any:
    assert await _run_ptt_on(stack, handler) == {}


def _gate_enqueue_after_failure(
    server: WebServer,
) -> tuple[asyncio.Event, asyncio.Event]:
    """Park the queue's failure between the executor and the handler."""
    enqueue_managed_positive_tx = server.enqueue_managed_positive_tx
    parked = asyncio.Event()
    gate = asyncio.Event()

    async def enqueue(*args: Any, **kwargs: Any) -> Any:
        try:
            return await enqueue_managed_positive_tx(*args, **kwargs)
        except BaseException:
            parked.set()
            await gate.wait()
            raise

    server.enqueue_managed_positive_tx = enqueue
    return parked, gate


def _handler_on(
    authority: Any, enqueue_managed_positive_tx: Any, name: str
) -> ControlHandler:
    """A handler on the queue-less seam (precedent: test_web_managed_tx_ingress)."""
    server = SimpleNamespace(
        command_queue=MagicMock(),
        enqueue_managed_positive_tx=enqueue_managed_positive_tx,
    )
    return ControlHandler(
        MagicMock(),
        _radio(),
        "test",
        "IC-9700",
        server=server,
        managed_tx_authority=authority,
        session_id=f"websocket-{name}",
    )


def _direct_handler(stack: _Stack, name: str) -> ControlHandler:
    async def enqueue(*, ready: asyncio.Future[None], submission: Any, **_: Any) -> Any:
        ready.set_result(None)
        return await submission

    return _handler_on(stack.authority, enqueue, name)


async def _release(authority: ManagedTxAuthority) -> None:
    if (await authority.snapshot()).state.intent.kind is not ManagedTxIntentKind.RX:
        submission = await authority.submit_force_off()
        await submission.wait_settlement()
    await authority.close()


@pytest.mark.parametrize(
    ("name", "params", "result"),
    (("ptt_on", {}, {}), ("ptt", {"state": True}, {"state": True})),
    ids=("ptt_on", "ptt_alias"),
)
async def test_first_client_ptt_admits_through_the_shared_queue(
    name: str, params: dict[str, Any], result: dict[str, Any]
) -> None:
    stack = _QueueStack()
    try:
        stack.enqueued.clear()
        task = asyncio.create_task(stack.handler("a")._enqueue_command(name, params))
        await _wait_barrier(stack.enqueued)
        await _drain_one(stack)
        assert await task == result
        assert stack.authority.tx_holder_nowait() == "websocket-a"
        assert stack.queue.pending_count == 0
        assert [effect.operation for effect in stack.lane.effects] == [
            ActuationOperation.PTT_ON
        ]
        stack.radio.set_ptt.assert_not_awaited()
    finally:
        await _release(stack.authority)


async def test_the_holders_repeat_ptt_on_stays_accepted_without_new_effects() -> None:
    stack = _QueueStack()
    try:
        holder = stack.handler("a")
        await _key(stack, holder)
        await _key(stack, holder)
        assert stack.authority.tx_holder_nowait() == "websocket-a"
        assert [effect.operation for effect in stack.lane.effects] == [
            ActuationOperation.PTT_ON
        ]
    finally:
        await _release(stack.authority)


async def test_the_holder_releases_with_ptt_off() -> None:
    stack = _QueueStack()
    try:
        holder = stack.handler("a")
        await _key(stack, holder)
        assert await holder._enqueue_command("ptt_off", {}) == {}
        assert stack.authority.tx_holder_nowait() is None
        assert stack.lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    finally:
        await _release(stack.authority)


async def test_second_client_ws_envelope_carries_the_conflict_message() -> None:
    stack = _QueueStack()
    try:
        await _key(stack, stack.handler("a"))
        ws = SimpleNamespace(send_text=AsyncMock())
        stack.enqueued.clear()
        task = asyncio.create_task(
            stack.handler("b", ws=ws)._dispatch_command("cmd-1", "ptt_on", {})
        )
        await _wait_barrier(stack.enqueued)
        await _drain_one(stack)
        await task
        response = decode_json(ws.send_text.await_args.args[0])
        assert response["id"] == "cmd-1"
        assert response["ok"] is False
        assert response["error"] == "radio_nak"
        assert _HOLDER_CONFLICT in response["message"]
    finally:
        await _release(stack.authority)


async def test_holder_released_before_the_handler_sees_the_refusal() -> None:
    stack = _QueueStack()
    try:
        await _key(stack, stack.handler("a"))
        parked, gate = _gate_enqueue_after_failure(stack.server)
        stack.enqueued.clear()
        task = asyncio.create_task(stack.handler("b")._enqueue_command("ptt_on", {}))
        await _wait_barrier(stack.enqueued)
        await _drain_one(stack)
        await _wait_barrier(parked)
        # The refusal is fixed; the holder lets go before the handler sees it.
        await stack.authority.ptt_up("websocket-a")
        assert stack.authority.tx_holder_nowait() is None
        gate.set()
        with pytest.raises(CommandRejectedError) as excinfo:
            await task
        assert _HOLDER_CONFLICT in str(excinfo.value)
    finally:
        await _release(stack.authority)


async def test_holder_appearing_before_the_handler_sees_an_infra_refusal() -> None:
    stack = _QueueStack()
    try:
        await stack.authority.provider_unavailable()
        parked, gate = _gate_enqueue_after_failure(stack.server)
        stack.enqueued.clear()
        task = asyncio.create_task(stack.handler("b")._enqueue_command("ptt_on", {}))
        await _wait_barrier(stack.enqueued)
        await _drain_one(stack)
        await _wait_barrier(parked)
        # The receipt said RX; a holder keys before the handler sees it.
        await stack.authority.provider_available(8)
        await _key(stack, stack.handler("a"))
        assert stack.authority.held_by_other("websocket-b") is True
        gate.set()
        with pytest.raises(CommandRejectedError) as excinfo:
            await task
        assert str(excinfo.value) == _UNAVAILABLE
    finally:
        await _release(stack.authority)


async def test_missing_authority_keeps_the_unavailable_refusal() -> None:
    handler = ControlHandler(
        MagicMock(), _radio(), "test", "IC-9700", managed_tx_authority=None
    )
    with pytest.raises(CommandRejectedError, match=_UNAVAILABLE):
        await handler._enqueue_command("ptt_on", {})


async def test_ptt_without_a_command_queue_keeps_the_unavailable_refusal() -> None:
    stack = _QueueStack()
    try:
        handler = ControlHandler(
            MagicMock(),
            stack.radio,
            "test",
            "IC-9700",
            server=None,
            managed_tx_authority=stack.authority,
        )
        with pytest.raises(CommandRejectedError, match=_UNAVAILABLE):
            await handler._enqueue_command("ptt_on", {})
        assert stack.queue.pending_count == 0
    finally:
        await _release(stack.authority)


async def test_session_gone_before_drain_keeps_the_unavailable_refusal() -> None:
    stack = _QueueStack()
    try:
        second = stack.handler("b")
        stack.queue.register_session("websocket-b")
        stack.enqueued.clear()
        task = asyncio.create_task(second._enqueue_command("ptt_on", {}))
        await _wait_barrier(stack.enqueued)
        stack.queue.unregister_session("websocket-b")
        await _drain_one(stack)
        with pytest.raises(CommandRejectedError, match=_UNAVAILABLE):
            await task
        assert stack.authority.tx_holder_nowait() is None
    finally:
        await _release(stack.authority)


async def test_provider_loss_rejection_keeps_the_unavailable_refusal() -> None:
    stack = _QueueStack()
    try:
        await stack.authority.provider_unavailable()
        with pytest.raises(CommandRejectedError, match=_UNAVAILABLE):
            await _run_ptt_on(stack, stack.handler("b"))
        assert (await stack.authority.snapshot()).state.intent.kind is (
            ManagedTxIntentKind.RX
        )
    finally:
        await _release(stack.authority)


async def test_expired_admission_keeps_the_unavailable_refusal() -> None:
    stack = _QueueStack()
    try:
        stack.clock.bias = 20.0
        with pytest.raises(CommandRejectedError, match=_UNAVAILABLE):
            await _run_ptt_on(stack, stack.handler("b"))
        assert (await stack.authority.snapshot()).state.intent.kind is (
            ManagedTxIntentKind.RX
        )
    finally:
        await _release(stack.authority)


async def test_ptt_over_a_latched_transmit_keeps_the_unavailable_refusal() -> None:
    stack = _QueueStack()
    try:
        assert await stack.authority.transmit_on() is ManagedTxOutcome.ACCEPTED
        # TRANSMIT intents carry no owner in state, so the receipt cannot
        # prove a different client holds; the wording stays as-is.
        with pytest.raises(CommandRejectedError, match=_UNAVAILABLE):
            await _run_ptt_on(stack, stack.handler("b"))
        assert (await stack.authority.snapshot()).state.intent.kind is (
            ManagedTxIntentKind.TRANSMIT
        )
    finally:
        await _release(stack.authority)


@pytest.mark.parametrize(
    ("name", "params"),
    (("ptt_on", {}), ("ptt_off", {})),
    ids=("key", "unkey"),
)
async def test_direct_non_holder_ptt_is_told_who_holds_the_transmitter(
    name: str, params: dict[str, Any]
) -> None:
    stack = _Stack()
    try:
        assert await stack.authority.ptt_down("websocket-a") is (
            ManagedTxOutcome.ACCEPTED
        )
        with pytest.raises(CommandRejectedError, match=_HOLDER_CONFLICT):
            await _direct_handler(stack, "b")._enqueue_command(name, params)
        assert stack.authority.tx_holder_nowait() == "websocket-a"
    finally:
        await _release(stack.authority)


async def test_direct_rejection_without_a_holder_keeps_admission_wording() -> None:
    stack = _Stack()
    try:
        await stack.authority.provider_unavailable()
        with pytest.raises(CommandRejectedError, match=_ADMISSION_REJECTED):
            await _direct_handler(stack, "b")._enqueue_command("ptt_on", {})
        assert (await stack.authority.snapshot()).state.intent.kind is (
            ManagedTxIntentKind.RX
        )
    finally:
        await _release(stack.authority)


@pytest.mark.parametrize(
    "error",
    (RuntimeError("queue went away"), TimeoutError()),
    ids=("runtime", "timeout"),
)
async def test_unrelated_failures_after_a_rejected_receipt_stay_unavailable(
    error: Exception,
) -> None:
    stack = _Stack()
    try:
        assert await stack.authority.ptt_down("websocket-a") is (
            ManagedTxOutcome.ACCEPTED
        )

        async def enqueue(
            *, ready: asyncio.Future[None], submission: Any, **_: Any
        ) -> Any:
            ready.set_result(None)
            await asyncio.shield(submission)
            raise error

        with pytest.raises(CommandRejectedError, match=_UNAVAILABLE):
            await _handler_on(stack.authority, enqueue, "b")._enqueue_command(
                "ptt_on", {}
            )
        assert stack.authority.tx_holder_nowait() == "websocket-a"
    finally:
        await _release(stack.authority)


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (CommandError("positive TX admission was rejected"), _HOLDER_CONFLICT),
        (CommandError("positive TX queue entry was cancelled"), _UNAVAILABLE),
    ),
    ids=("admission_rejected", "other_queue_error"),
)
async def test_queue_exception_translates_only_the_admission_rejection(
    error: Exception, expected: str
) -> None:
    held = ManagedTxState(
        intent=ManagedTxIntent.ptt("websocket-a"),
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=1.0,
    )
    transition = ManagedTxTransition(held, ManagedTxOutcome.REJECTED)

    async def completion() -> Any:
        return (transition, None)

    receipt = ManagedTxSubmission(transition, asyncio.create_task(completion()))

    class _Authority:
        def start_ptt_submission(
            self, on: bool, owner: str, *, ready: Any, expires_at_monotonic: Any
        ) -> Any:
            async def deliver() -> ManagedTxSubmission:
                return receipt

            return asyncio.create_task(deliver())

    async def enqueue(*, ready: asyncio.Future[None], submission: Any, **_: Any) -> Any:
        ready.set_result(None)
        await asyncio.shield(submission)
        raise error

    handler = _handler_on(_Authority(), enqueue, "b")
    with pytest.raises(CommandRejectedError, match=expected):
        await handler._enqueue_command("ptt_on", {})
