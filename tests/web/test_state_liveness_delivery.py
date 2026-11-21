"""MOR-4244: liveness-only state changes reach clients at most once per second.

See ``docs/architecture/state-delivery-liveness.md``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from serial_stub import SerialMockRadio
from test_managed_tx_authority import FakeConfigStore, FakeFence
from test_web_auth_compare_digest import _MemoryWriter

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.state_pipeline_contracts import (
    CommandLifecycleEvent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.radio import IcomRadio
from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
    ManagedTxIntentKind,
)
from rigplane.testing.timing import VirtualClock
from rigplane.web import runtime_helpers
from rigplane.web import server as server_module
from rigplane.web._delta_encoder import apply_delta
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import (
    COMMAND_FOLLOWUP_WINDOW_S,
    LIVENESS_DELIVERY_INTERVAL_S,
    WebConfig,
    WebServer,
)
from rigplane.ws import WS_OP_TEXT, WebSocketConnection, make_frame

PTT = FieldPath.global_("tx_state", "ptt")
FREQ = FieldPath.active("0", "freq_mode", "freq_hz")
# The PTT poll alone runs at ~20/s on a live Icom; 1/16 s keeps the clock exact
# and clears the 50 ms coalescing window on every answer.
POLL_PERIOD_S = 0.0625


class _GainRadio(SerialMockRadio):
    def __init__(self) -> None:
        super().__init__(model="IC-7610")
        self.gain_writes: list[tuple[int, int]] = []

    def supports_command(self, command: str, *, receiver: int = 0) -> bool:
        return command == "set_rf_gain" and receiver == 0

    async def set_rf_gain(self, level: int, receiver: int = 0) -> None:
        self.gain_writes.append((level, receiver))
        await super().set_rf_gain(level, receiver=receiver)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    """One virtual clock for the server's throttle and the liveness age."""
    virtual = VirtualClock(start=1000.0)
    virtual.scope_to(monkeypatch, server_module)
    # runtime_helpers reads only ``time``; share the server module's view of it.
    monkeypatch.setattr(runtime_helpers, "time", server_module.time)
    return virtual


def _observation(
    path: FieldPath, value: object, at: float, *, max_age: float | None = None
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=SourceMetadata(source="poll_response", provider="icom_civ"),
        timestamp_monotonic=at,
        max_age=max_age,
    )


def _server() -> tuple[WebServer, IcomRadio, BoundedQueue[dict[str, Any]]]:
    radio = IcomRadio("127.0.0.1", model="IC-7610")
    srv = WebServer(radio, WebConfig(radio_model="IC-7610"))
    q: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=4096)
    return srv, radio, q


def _drain(q: BoundedQueue[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    while not q.empty():
        event = q.get_nowait()
        if event["type"] == "state_update":
            events.append(event["data"])
    return events


def _poll(srv: WebServer, radio: IcomRadio, clock: VirtualClock, value: object) -> None:
    """One radio answer that re-confirms ``ptt``, then the server's reaction."""
    radio.last_valid_frame_monotonic = clock.now
    srv.command_state_store.apply(_observation(PTT, value, clock.now))
    srv._broadcast_state_update()  # noqa: SLF001


def test_idle_radio_delivers_liveness_at_most_once_per_second(
    clock: VirtualClock,
) -> None:
    srv, radio, q = _server()
    _poll(srv, radio, clock, False)
    srv.register_control_event_queue(q)
    _drain(q)

    seconds = 10
    for _ in range(int(seconds / POLL_PERIOD_S)):
        clock.advance(POLL_PERIOD_S)
        _poll(srv, radio, clock, False)

    deliveries = _drain(q)
    polls = int(seconds / POLL_PERIOD_S)
    # Before MOR-4244 every one of the 160 answers delivered a state_update.
    assert len(deliveries) <= seconds / LIVENESS_DELIVERY_INTERVAL_S + 1
    assert len(deliveries) >= seconds / LIVENESS_DELIVERY_INTERVAL_S - 1
    seqs = [delivery["observationSeq"] for delivery in deliveries]
    assert seqs == sorted(set(seqs))
    # The newest delivery is at most one interval of answers behind the store.
    behind = srv.command_state_store.snapshot().observation_seq - seqs[-1]
    assert behind <= LIVENESS_DELIVERY_INTERVAL_S / POLL_PERIOD_S
    assert polls == 160


def test_value_change_right_after_a_delivery_goes_out_at_once(
    clock: VirtualClock,
) -> None:
    srv, radio, q = _server()
    srv.command_state_store.apply(_observation(FREQ, 14_074_000, clock.now))
    _poll(srv, radio, clock, False)
    srv.register_control_event_queue(q)
    _drain(q)

    clock.advance(0.1)
    _poll(srv, radio, clock, False)  # liveness only: held back
    assert _drain(q) == []

    clock.advance(0.1)
    srv.command_state_store.apply(_observation(FREQ, 14_075_000, clock.now))
    srv._broadcast_state_update()  # noqa: SLF001

    [delta] = _drain(q)
    assert delta["changed"]["main"]["freqHz"] == 14_075_000
    # The delivered delta also carries the held-back liveness.
    assert delta["observationSeq"] == srv.command_state_store.snapshot().observation_seq


def test_same_value_change_of_observation_kind_goes_out_at_once(
    clock: VirtualClock,
) -> None:
    srv, _radio, q = _server()
    srv.command_state_store.apply(_observation(FREQ, 14_074_000, clock.now))
    srv.register_control_event_queue(q)
    _drain(q)

    clock.advance(0.1)
    srv.command_state_store.apply(
        Observation(
            path=FREQ,
            value=14_074_000,
            source=SourceMetadata(source="command_response", provider="icom_civ"),
            timestamp_monotonic=clock.now,
        )
    )
    srv._broadcast_state_update()  # noqa: SLF001

    [delta] = _drain(q)
    status = delta["changed"]["fieldStatus"]["main.freqHz"]
    assert status["source"]["source"] == "command_response"


def test_read_provenance_alone_is_liveness(clock: VirtualClock) -> None:
    """A re-read through another poll (other max age) only proves liveness."""
    srv, _radio, q = _server()
    srv.command_state_store.apply(
        _observation(FREQ, 14_074_000, clock.now, max_age=1.0)
    )
    srv.register_control_event_queue(q)
    _drain(q)

    clock.advance(0.1)
    srv.command_state_store.apply(
        _observation(FREQ, 14_074_000, clock.now, max_age=10.0)
    )
    srv._broadcast_state_update()  # noqa: SLF001
    assert _drain(q) == []


def test_command_activity_lifts_the_hold_for_the_follow_up_window(
    clock: VirtualClock,
) -> None:
    srv, radio, q = _server()
    _poll(srv, radio, clock, False)
    srv.register_control_event_queue(q)
    _drain(q)

    srv._on_command_lifecycle_event(  # noqa: SLF001
        CommandLifecycleEvent(
            command_id="cmd-1",
            state="acknowledged",
            timestamp_monotonic=clock.now,
            source="test",
        )
    )
    # A same-value read-back right after the ack confirms the command at once.
    clock.advance(0.1)
    _poll(srv, radio, clock, False)
    [readback] = _drain(q)
    assert (
        readback["observationSeq"] == srv.command_state_store.snapshot().observation_seq
    )

    clock.advance(COMMAND_FOLLOWUP_WINDOW_S)
    _poll(srv, radio, clock, False)  # 1 s since the last delivery has passed
    _drain(q)
    clock.advance(0.1)
    _poll(srv, radio, clock, False)  # window over: held back again
    assert _drain(q) == []


async def test_held_back_liveness_is_flushed_when_the_interval_ends(
    clock: VirtualClock,
) -> None:
    srv, radio, q = _server()
    _poll(srv, radio, clock, False)
    srv.register_control_event_queue(q)
    _drain(q)

    clock.advance(0.25)
    _poll(srv, radio, clock, False)
    assert _drain(q) == []

    # The deferred delivery sleeps out the rest of the interval, then flushes.
    for _ in range(3):
        await asyncio.sleep(0)

    assert clock.sleeps == [pytest.approx(LIVENESS_DELIVERY_INTERVAL_S - 0.25)]
    [flushed] = _drain(q)
    assert (
        flushed["observationSeq"] == srv.command_state_store.snapshot().observation_seq
    )


@pytest.mark.parametrize("query", ["", "?state_delta=2"])
def test_held_target_observation_precedes_ack_over_ws(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    monkeypatch.setattr(server_module, "asyncio", asyncio)

    async def scenario() -> None:
        radio = _GainRadio()
        await radio.connect()
        await SerialMockRadio.set_rf_gain(radio, 128, receiver=0)
        srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        gain = FieldPath.receiver("0", "operator_controls", "rf_gain")
        held_delivery = asyncio.Event()

        async def park_delivery(_delay: float) -> None:
            # No timer can release the held observation before the ACK.
            await held_delivery.wait()

        monkeypatch.setattr(srv, "_delayed_state_broadcast", park_delivery)

        def observe(path: FieldPath, value: object) -> None:
            srv.command_state_store.apply(_observation(path, value, clock.now))
            srv._broadcast_state_update()  # noqa: SLF001

        srv.command_state_store.apply(_observation(gain, 128, clock.now))
        srv.command_state_store.apply(_observation(PTT, False, clock.now))
        # Only start the real listener; no poller consumes the command yet.
        srv._server = await asyncio.start_server(  # noqa: SLF001
            srv._accept_client,
            "127.0.0.1",
            0,  # noqa: SLF001
        )
        try:
            async with aiohttp.ClientSession() as session:
                url = f"ws://127.0.0.1:{srv.port}/api/v1/ws{query}"
                async with session.ws_connect(url) as ws:
                    state: dict[str, Any] = {}

                    async def receive_until(
                        matches: Callable[[dict[str, Any]], bool],
                    ) -> dict[str, Any]:
                        nonlocal state
                        while True:
                            frame = await ws.receive()
                            assert frame.type == aiohttp.WSMsgType.TEXT
                            message = json.loads(frame.data)
                            if message["type"] == "state_update":
                                state = apply_delta(state, message["data"])
                            if matches(message):
                                return message

                    await receive_until(lambda msg: msg["type"] == "state_update")
                    assert (
                        state["fieldStatus"]["main.rfGain"]["lastObservedMonotonic"]
                        == 1000.0
                    )
                    assert state["main"]["rfGain"] == pytest.approx(128 / 255)

                    clock.advance(0.1)
                    observe(gain, 128)  # 1000.1: held pre-command observation
                    assert srv._pending_liveness_delivery_task is not None  # noqa: SLF001

                    clock.advance(0.1)
                    await ws.send_json(
                        {
                            "type": "cmd",
                            "id": "same-gain",
                            "name": "set_rf_gain",
                            "params": {"level": 128, "receiver": 0},
                        }
                    )
                    ack = await receive_until(
                        lambda msg: (
                            msg["type"] == "response" and msg["id"] == "same-gain"
                        )
                    )
                    assert ack["ok"] is True
                    ack_boundary = state["fieldStatus"]["main.rfGain"][
                        "lastObservedMonotonic"
                    ]
                    assert radio.gain_writes == []

                    clock.advance(0.1)
                    observe(PTT, False)  # 1000.3: unrelated poll releases the hold
                    await receive_until(
                        lambda msg: (
                            msg["type"] == "state_update"
                            and state["fieldStatus"]["ptt"]["lastObservedMonotonic"]
                            == clock.now
                        )
                    )
                    assert radio.gain_writes == []
                    assert ack_boundary == 1000.1
                    assert (
                        state["fieldStatus"]["main.rfGain"]["lastObservedMonotonic"]
                        == ack_boundary
                    )
                    assert state["main"]["rfGain"] == pytest.approx(128 / 255)

                    poller = RadioPoller(
                        radio,
                        srv.command_queue,
                        state_store=srv.command_state_store,
                        clock=clock,
                    )
                    [entry] = srv.command_queue.drain_entries()
                    await poller._execute_queued_entry(entry)  # noqa: SLF001
                    assert radio.gain_writes == [(128, 0)]
                    clock.advance(0.1)
                    observe(gain, await radio.get_rf_gain(receiver=0))
                    await receive_until(
                        lambda msg: (
                            msg["type"] == "state_update"
                            and state["fieldStatus"]["main.rfGain"][
                                "lastObservedMonotonic"
                            ]
                            == clock.now
                        )
                    )
                    assert (
                        state["fieldStatus"]["main.rfGain"]["lastObservedMonotonic"]
                        > ack_boundary
                    )
        finally:
            await srv.stop()

    clock.run(scenario)


@pytest.mark.parametrize(
    "failure", ["sender_error", "baseline_overflow", "fanout_overflow"]
)
def test_outbound_failure_drains_pending_managed_ptt_then_releases(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(server_module, "asyncio", asyncio)

    async def scenario() -> None:
        radio = _GainRadio()
        await radio.connect()
        radio._civ_epoch = 1  # noqa: SLF001
        srv = WebServer(radio, WebConfig(radio_model="IC-7610"))
        actuator = _RadioPttActuator(radio)
        lane = ManagedTxEffectLane(actuator, clock=clock)
        authority = ManagedTxAuthority(
            lane, FakeConfigStore(), FakeFence(), provider_generation=7, clock=clock
        )
        poller = RadioPoller(
            radio,
            srv.command_queue,
            state_store=srv.command_state_store,
            managed_tx_authority=authority,
            clock=clock,
        )
        assert srv.command_queue.capture_connection_generation() == radio._civ_epoch  # noqa: SLF001
        reader = asyncio.StreamReader()
        writer = _StalledWriter(reader)
        ws = WebSocketConnection(reader, writer, clock=clock)  # type: ignore[arg-type]
        handler = ControlHandler(
            ws,
            radio,
            "test",
            "IC-7610",
            server=srv,
            session_id="pending-ptt",
            managed_tx_authority=authority,
            clock=clock,
        )
        disconnect = AsyncMock(wraps=authority.owner_disconnect)
        monkeypatch.setattr(authority, "owner_disconnect", disconnect)
        unregister = Mock(wraps=srv.unregister_control_event_queue)
        monkeypatch.setattr(srv, "unregister_control_event_queue", unregister)
        task = asyncio.create_task(handler.run())
        consumer: asyncio.Task[None] | None = None
        poller_release = asyncio.Event()
        try:
            await clock.elapse(0)
            q = handler._event_queue  # noqa: SLF001
            assert srv.command_queue.session_is_live("pending-ptt")
            assert q in srv._control_event_queues  # noqa: SLF001
            writer.stall = True
            q.put_nowait({"type": "notification"})
            await writer.draining.wait()
            reader.feed_data(
                make_frame(
                    WS_OP_TEXT,
                    json.dumps(
                        {"type": "cmd", "id": "key", "name": "ptt_on", "params": {}}
                    ).encode(),
                )
            )
            await clock.elapse(0)
            entry = srv.command_queue.take_entry()
            assert entry is not None
            assert entry.positive_tx_submission is not None
            assert not entry.positive_tx_submission.done()
            assert entry.positive_tx_ready is not None
            assert not entry.positive_tx_ready.done()
            assert entry.future is not None and not entry.future.done()
            assert not task.done()
            assert not actuator.operations

            async def consume() -> None:
                await poller_release.wait()
                await poller._execute_queued_entry(entry)  # noqa: SLF001

            consumer = asyncio.create_task(consume())
            if failure == "sender_error":
                writer.release.set()
            else:
                for command_id in range(q.maxsize):
                    q.put_nowait({"type": "response", "id": command_id, "ok": True})
                if failure == "baseline_overflow":
                    srv.enqueue_control_state_baseline(q)
                else:
                    srv._broadcast_state_update(force=True)  # noqa: SLF001
            await clock.elapse(0)
            assert not task.done()
            assert not entry.positive_tx_submission.done()
            assert writer.closed
            poller_release.set()
            await clock.elapse(0)

            assert task.done()
            assert consumer.done()
            await consumer
            assert ActuationOperation.PTT_ON in actuator.operations
            assert actuator.operations[-1] is ActuationOperation.FORCE_RECEIVE
            assert radio._ptt is False  # noqa: SLF001
            assert (
                await authority.snapshot()
            ).state.intent.kind is ManagedTxIntentKind.RX
            assert not (await authority.snapshot()).state.release_required
            assert authority.tx_holder_nowait() is None
            assert entry.positive_tx_submission.done()
            assert entry.future.done() and not entry.future.cancelled()
            assert entry.future.exception() is None
            disconnect.assert_awaited_once_with("pending-ptt")
            assert writer.closed
            assert not srv.command_queue.session_is_live("pending-ptt")
            assert not srv.command_queue.has_commands
            assert q not in srv._control_event_queues  # noqa: SLF001
            assert "pending-ptt" not in srv._session_queues  # noqa: SLF001
            unregister.assert_called_once_with(q, session_id="pending-ptt")
            assert not handler._cmd_pending  # noqa: SLF001
            assert not handler._cmd_flush_tasks  # noqa: SLF001
            [outcome] = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(outcome, (ConnectionError, asyncio.CancelledError))
        finally:
            poller_release.set()
            task.cancel()
            if consumer is not None:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            await asyncio.gather(task, return_exceptions=True)
            await authority.close()
            await srv.stop()

    clock.run(scenario)


class _RadioPttActuator:
    def __init__(self, radio: _GainRadio) -> None:
        self.radio = radio
        self.operations: list[ActuationOperation | AbortOperation] = []

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if not is_current():
            return ActuationResult.REJECTED
        self.operations.append(operation)
        if isinstance(operation, ActuationOperation):
            await self.radio.set_ptt(operation is not ActuationOperation.FORCE_RECEIVE)
        return ActuationResult.ACCEPTED


def test_authority_identifies_the_keying_channel_during_preparation_and_detach(
    clock: VirtualClock,
) -> None:
    async def scenario() -> None:
        radio = _GainRadio()
        lane = ManagedTxEffectLane(_RadioPttActuator(radio), clock=clock)
        authority = ManagedTxAuthority(
            lane, FakeConfigStore(), FakeFence(), provider_generation=7, clock=clock
        )
        controller = ControllerAuthority(
            ready=lambda: True,
            cleanup=AsyncMock(),
            clock=clock,
            release_session=authority.owner_disconnect,
        )
        authority.bind_controller(controller)
        key = controller.acquire("browser")["controller_key"]
        primary = controller.attach(key, "primary", "primary")
        auxiliary = controller.attach(key, "auxiliary", "auxiliary")
        owner = auxiliary.tx_owner
        assert owner is not None
        entered = asyncio.Event()
        proceed = asyncio.Event()

        async def before_on() -> bool:
            entered.set()
            await proceed.wait()
            return True

        with controller.bind(auxiliary):
            pending = authority.start_ptt_submission(True, owner, before_on=before_on)
        try:
            await entered.wait()
            assert authority.ptt_channel_holds_nowait(owner, "auxiliary")
            assert not authority.ptt_channel_holds_nowait(owner, "primary")
            proceed.set()
            submission = await pending
            await submission.wait_settlement()
            controller.detach(auxiliary)
            controller.validate(primary)
            assert authority.ptt_channel_holds_nowait(owner, "auxiliary")
            await authority.owner_disconnect(owner)
            assert not authority.ptt_channel_holds_nowait(owner, "auxiliary")
            assert not authority.ptt_channel_holds_nowait(owner, "primary")
            controller.validate(primary)
        finally:
            proceed.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await controller.release(key)
            await authority.close()

    clock.run(scenario)


@pytest.mark.parametrize(
    "failure",
    ["sender_error", "baseline_overflow", "fanout_overflow", "buffered_burst"],
)
@pytest.mark.parametrize(
    ("name", "params"), [("ptt_off", {}), ("ptt", {"state": False})]
)
def test_auxiliary_outbound_failure_before_key_up_admission_releases_radio(
    clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    name: str,
    params: dict[str, Any],
) -> None:
    monkeypatch.setattr(server_module, "asyncio", asyncio)

    async def scenario() -> None:
        radio = _GainRadio()
        await radio.connect()
        radio._civ_epoch = 1  # noqa: SLF001
        srv = WebServer(radio, WebConfig(radio_model="IC-7610"))
        lane = ManagedTxEffectLane(_RadioPttActuator(radio), clock=clock)
        authority = ManagedTxAuthority(
            lane, FakeConfigStore(), FakeFence(), provider_generation=7, clock=clock
        )
        controller = srv._controller  # noqa: SLF001
        monkeypatch.setattr(controller, "_clock", clock)
        authority.bind_controller(controller)
        radio._controller_authority = controller  # noqa: SLF001
        key = controller.acquire("browser")["controller_key"]
        primary = controller.attach(key, "primary", "live-primary")
        auxiliary = controller.attach(key, "auxiliary", "releasing-auxiliary")
        assert auxiliary.tx_owner is not None
        poller = RadioPoller(
            radio,
            srv.command_queue,
            state_store=srv.command_state_store,
            managed_tx_authority=authority,
            clock=clock,
        )
        readers = [asyncio.StreamReader(), asyncio.StreamReader()]
        writers = [_StalledWriter(reader) for reader in readers]
        handlers = [
            ControlHandler(
                WebSocketConnection(reader, writer, clock=clock),  # type: ignore[arg-type]
                radio,
                "test",
                "IC-7610",
                server=srv,
                session_id=ticket.session_id,
                tx_owner=ticket.tx_owner,
                managed_tx_authority=authority,
                clock=clock,
            )
            for reader, writer, ticket in zip(readers, writers, (primary, auxiliary))
        ]
        tasks = []
        for handler, ticket in zip(handlers, (primary, auxiliary)):
            controller.register_control(ticket)
            controller.add_channel(ticket, handler._ws.abort)  # noqa: SLF001
            with controller.bind(ticket):
                tasks.append(asyncio.create_task(handler.run()))
        release_started = asyncio.Event()
        submit_ptt = authority.submit_ptt

        async def observe_release(*args: Any, **kwargs: Any) -> Any:
            release_started.set()
            return await submit_ptt(*args, **kwargs)

        monkeypatch.setattr(authority, "submit_ptt", observe_release)

        def command(
            command_id: str, command_name: str, command_params: dict[str, Any]
        ) -> None:
            readers[1].feed_data(
                make_frame(
                    WS_OP_TEXT,
                    json.dumps(
                        {
                            "type": "cmd",
                            "id": command_id,
                            "name": command_name,
                            "params": command_params,
                        }
                    ).encode(),
                )
            )

        try:
            await clock.elapse(0)
            command("key", "ptt_on", {})
            await clock.elapse(0)
            entry = srv.command_queue.take_entry()
            assert entry is not None
            await poller._execute_queued_entry(entry)  # noqa: SLF001
            await clock.elapse(0)
            assert radio._ptt is True  # noqa: SLF001
            assert authority.tx_holder_nowait() == auxiliary.tx_owner
            if failure == "buffered_burst":
                send = AsyncMock(wraps=handlers[1]._ws.send_text)  # noqa: SLF001
                monkeypatch.setattr(handlers[1]._ws, "send_text", send)  # noqa: SLF001
                for command_id in range(120):
                    command(str(command_id), "switch_scope_receiver", {"receiver": 0})
                command("unkey", name, params)
                await clock.elapse(0)
                responses = [
                    json.loads(call.args[0])
                    for call in send.call_args_list
                    if json.loads(call.args[0]).get("type") == "response"
                ]
                assert [response["id"] for response in responses] == [
                    *(str(i) for i in range(120)),
                    "unkey",
                ]
                assert all(response["ok"] for response in responses)
                assert radio._ptt is False  # noqa: SLF001
                state = (await authority.snapshot()).state
                assert state.intent.kind is ManagedTxIntentKind.RX
                assert not state.release_required
                assert not writers[1].closed
                assert not tasks[1].done()
                assert not tasks[0].done()
                return
            q = handlers[1]._event_queue  # noqa: SLF001
            writers[1].stall = True
            q.put_nowait({"type": "notification"})
            await writers[1].draining.wait()
            async with authority._lock:  # noqa: SLF001
                command("unkey", name, params)
                await release_started.wait()
                await clock.elapse(0)
                assert radio._ptt is True  # noqa: SLF001
                assert not tasks[1].done()
                if failure == "sender_error":
                    writers[1].release.set()
                else:
                    for command_id in range(q.maxsize):
                        q.put_nowait({"type": "response", "id": command_id, "ok": True})
                    if failure == "baseline_overflow":
                        srv.enqueue_control_state_baseline(q)
                    else:
                        srv._broadcast_state_update(force=True)  # noqa: SLF001
                await clock.elapse(0)
            await clock.elapse(0)
            assert radio._ptt is False  # noqa: SLF001
            state = (await authority.snapshot()).state
            assert state.intent.kind is ManagedTxIntentKind.RX
            assert not state.release_required
            assert authority.tx_holder_nowait() is None
            assert tasks[1].done()
            await asyncio.gather(tasks[1], return_exceptions=True)
            assert q not in srv._control_event_queues  # noqa: SLF001
            assert not srv.command_queue.session_is_live("releasing-auxiliary")
            assert not srv.command_queue.has_commands
            assert writers[1].closed
            assert not tasks[0].done()
            controller.validate(primary)
            assert controller.heartbeat(primary)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await authority.force_off()
            await authority.close()
            await srv.stop()

    clock.run(scenario)


class _StalledWriter(_MemoryWriter):
    def __init__(self, reader: asyncio.StreamReader) -> None:
        super().__init__()
        self.transport = SimpleNamespace(abort=self.abort)
        self.reader = reader
        self.stall = False
        self.draining = asyncio.Event()
        self.release = asyncio.Event()

    async def drain(self) -> None:
        if self.stall:
            self.draining.set()
            await self.release.wait()
            raise ConnectionError("peer stopped reading")

    def abort(self) -> None:
        self.close()
        self.reader.feed_eof()
        self.release.set()


@pytest.mark.parametrize(
    "failure", ["ack_overflow", "baseline_overflow", "fanout_overflow", "sender_error"]
)
def test_stalled_control_client_tears_down(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(server_module, "asyncio", asyncio)

    async def scenario() -> None:
        radio = _GainRadio()
        await radio.connect()
        srv = WebServer(radio, WebConfig(radio_model="IC-7610"))
        srv.command_queue.bind_connection_generation(lambda: radio)
        assert srv.command_queue.capture_connection_generation() is radio
        reader = asyncio.StreamReader()
        writer = _StalledWriter(reader)
        ws = WebSocketConnection(reader, writer, clock=clock)  # type: ignore[arg-type]
        disconnect = AsyncMock()
        handler = ControlHandler(
            ws,
            radio,
            "test",
            "IC-7610",
            server=srv,
            session_id="stalled-channel",
            tx_owner="ptt-owner",
            managed_tx_authority=SimpleNamespace(  # type: ignore[arg-type]
                owner_disconnect=disconnect,
                ptt_channel_holds_nowait=Mock(return_value=True),
            ),
            clock=clock,
        )
        task = asyncio.create_task(handler.run())
        try:
            await clock.elapse(0)
            assert srv.command_queue.session_is_live("stalled-channel")
            q = handler._event_queue  # noqa: SLF001
            assert q in srv._control_event_queues  # noqa: SLF001
            writer.stall = True
            q.put_nowait({"type": "notification"})
            await clock.elapse(0)
            assert writer.draining.is_set()

            if failure == "ack_overflow":
                for command_id in range(q.maxsize + 1):
                    reader.feed_data(
                        make_frame(
                            WS_OP_TEXT,
                            json.dumps(
                                {
                                    "type": "cmd",
                                    "id": command_id,
                                    "name": "set_rf_gain",
                                    "params": {"level": 128, "receiver": 0},
                                }
                            ).encode(),
                        )
                    )
                    await clock.elapse(POLL_PERIOD_S)
                    if task.done():
                        break
                assert command_id == q.maxsize - 1
                entries = srv.command_queue.drain_entries()
                assert entries[-1].command_id == str(command_id)
                assert entries[-1].connection_generation == (
                    srv.command_queue.capture_connection_generation()
                )
                queued = [q.get_nowait() for _ in range(q.qsize())]
                assert queued[0]["type"] == "state_update"
                responses = queued[1:]
                assert len(responses) == command_id
                assert all(
                    response["type"] == "response" and response["ok"] is True
                    for response in responses
                )
                assert [response["id"] for response in responses] == list(
                    range(command_id)
                )
            elif failure in {"baseline_overflow", "fanout_overflow"}:
                responses = [
                    {"type": "response", "id": i, "ok": True} for i in range(q.maxsize)
                ]
                for response in responses:
                    q.put_nowait(response)
                if failure == "baseline_overflow":
                    srv.enqueue_control_state_baseline(q)
                else:
                    srv._broadcast_state_update(force=True)  # noqa: SLF001
                assert writer.closed
                assert [q.get_nowait() for _ in range(q.qsize())] == responses
                await clock.elapse(0)
            else:
                writer.release.set()
                await clock.elapse(0)

            assert task.done()
            assert writer.closed
            disconnect.assert_awaited_once_with("ptt-owner")
            assert not srv.command_queue.session_is_live("stalled-channel")
            assert q not in srv._control_event_queues  # noqa: SLF001
            if failure == "sender_error":
                with pytest.raises(ConnectionError, match="peer stopped reading"):
                    await task
            else:
                [outcome] = await asyncio.gather(task, return_exceptions=True)
                assert outcome is None or isinstance(
                    outcome, (ConnectionError, asyncio.CancelledError)
                )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await srv.stop()

    clock.run(scenario)
