from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from rigplane.commands import CONTROLLER_ADDR
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.state_store import FreshnessState
from rigplane.runtime.radio import IcomRadio
from rigplane.types import CivFrame, bcd_encode
from rigplane.web.server import WebConfig, WebServer


def _frequency_frame(radio: IcomRadio) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=radio.profile.civ_addr,
        command=0x03,
        sub=None,
        data=bcd_encode(144_100_000),
    )


def _drain(queue: BoundedQueue[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def _cancel_delayed_broadcast(server: WebServer) -> None:
    task = server._pending_state_broadcast_task
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_unchanged_ic9700_civ_observation_reaches_control_ws() -> None:
    radio = IcomRadio("192.0.2.1", model="IC-9700")
    server = WebServer(radio, WebConfig(radio_model="IC-9700"))
    queue = BoundedQueue[dict[str, Any]](maxsize=64)
    radio.set_state_change_callback(server._on_radio_state_change)
    server.register_control_event_queue(queue)
    _drain(queue)
    frame = _frequency_frame(radio)
    generation = radio.state_store.provider_generation
    try:
        for second in range(8):
            now = 100.0 + second
            before = radio.state_store.snapshot()
            server._last_state_broadcast = 0.0
            with patch("rigplane.runtime._civ_rx.time.monotonic", return_value=now):
                await radio._civ_runtime._route_civ_frame(
                    frame,
                    generation=radio._civ_epoch,
                    store_provider_generation=generation,
                )
            updates = [
                event["data"]
                for event in _drain(queue)
                if event["type"] == "state_update"
            ]
            assert updates, f"accepted unchanged CI-V read at +{second}s was not sent"
            envelope = updates[-1]
            body = envelope["data"] if envelope["type"] == "full" else envelope["changed"]
            status = body["fieldStatus"]["main.freqHz"]
            assert status["lastObservedMonotonic"] == now
            assert envelope["observationSeq"] > before.observation_seq
            assert envelope["providerGeneration"] == generation
            after = radio.state_store.snapshot()
            assert after.field("receiver.0.active.freq_mode.freq_hz").freshness is (
                FreshnessState.FRESH
            )
            if second:
                assert after.state_revision == before.state_revision
            assert not server.build_public_state()["fieldStatus"]["active"]["observed"]
            await _cancel_delayed_broadcast(server)
    finally:
        await _cancel_delayed_broadcast(server)


@pytest.mark.parametrize("retired", ["civ", "provider"])
@pytest.mark.asyncio
async def test_rejected_ic9700_generation_does_not_refresh_control_ws(
    retired: str,
) -> None:
    radio = IcomRadio("192.0.2.1", model="IC-9700")
    server = WebServer(radio, WebConfig(radio_model="IC-9700"))
    queue = BoundedQueue[dict[str, Any]](maxsize=64)
    radio.set_state_change_callback(server._on_radio_state_change)
    server.register_control_event_queue(queue)
    provider_generation = radio.state_store.provider_generation
    civ_generation = radio._civ_epoch
    if retired == "civ":
        civ_generation -= 1
    else:
        radio.state_store.begin_provider_generation()
    _drain(queue)
    before = radio.state_store.snapshot()
    server._last_state_broadcast = 0.0
    try:
        with patch("rigplane.runtime._civ_rx.time.monotonic", return_value=100.0):
            await radio._civ_runtime._route_civ_frame(
                _frequency_frame(radio),
                generation=civ_generation,
                store_provider_generation=provider_generation,
            )
        assert _drain(queue) == []
        after = radio.state_store.snapshot()
        assert after.observation_seq == before.observation_seq
        assert after.state_revision == before.state_revision
        assert after.provider_generation == before.provider_generation
    finally:
        await _cancel_delayed_broadcast(server)
