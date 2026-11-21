"""MOR-4244: liveness-only state changes reach clients at most once per second.

See ``docs/architecture/state-delivery-liveness.md``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import aiohttp
import pytest

from serial_stub import SerialMockRadio

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.state_pipeline_contracts import (
    CommandLifecycleEvent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.radio import IcomRadio
from rigplane.testing.timing import VirtualClock
from rigplane.web import runtime_helpers
from rigplane.web import server as server_module
from rigplane.web._delta_encoder import apply_delta
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import (
    COMMAND_FOLLOWUP_WINDOW_S,
    LIVENESS_DELIVERY_INTERVAL_S,
    WebConfig,
    WebServer,
)

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
    # Use loop timers rather than scope_to's auto-advancing sleep: the held
    # delivery must stay parked while the real socket handles the command.
    monkeypatch.setattr(server_module, "asyncio", asyncio)

    async def scenario() -> None:
        radio = _GainRadio()
        await radio.connect()
        await SerialMockRadio.set_rf_gain(radio, 128, receiver=0)
        srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        gain = FieldPath.receiver("0", "operator_controls", "rf_gain")

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
