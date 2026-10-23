from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import aiohttp
import pytest

from rigplane.commands import parse_civ_frame
from rigplane.core.exceptions import BackgroundSendDropped
from rigplane.core.state_store import FreshnessState
from rigplane.runtime._state_queries import build_state_queries
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
from scripts.soak import soak_driver


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.01)


async def _get(session: aiohttp.ClientSession, base: str, path: str) -> dict[str, Any]:
    async with session.get(base + path) as response:
        assert response.status == 200
        return await response.json()


def _observed_mode_filter(stack: soak_driver._SimulatorStack) -> tuple[str, int]:
    snapshot = stack.radio.state_store.snapshot()
    return (
        snapshot.field("receiver.0.active.freq_mode.mode").value,
        snapshot.field("receiver.0.active.freq_mode.filter_num").value,
    )


@pytest.mark.parametrize("model", ["IC-7610", "IC-9700"])
async def test_cold_poll_enables_web_mode_filter_and_readback(model: str) -> None:
    stack = await soak_driver._start_simulator_stack(
        model=model, dual_receiver=True, enable_audio=False, capture_civ_frames=True
    )
    base = f"http://127.0.0.1:{stack.web_port}"
    client = soak_driver._TxSessionClient(base)
    try:
        await client.open()
        client.start_heartbeat()
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"X-RigPlane-Controller": client.key},
        ) as session:
            await _wait_until(
                lambda: (
                    stack.radio.state_store.snapshot()
                    .field("global.slow_state.active")
                    .freshness
                    is FreshnessState.FRESH
                )
            )
            cold = await _get(session, base, "/api/v1/state")
            assert cold["active"] == "MAIN"
            assert cold["fieldStatus"]["active"]["observed"] is True
            active = stack.radio.state_store.snapshot().field(
                "global.slow_state.active"
            )
            assert active.freshness is FreshnessState.FRESH
            assert active.source.provider == "icom_civ"
            assert any(
                frame[4:-1] == b"\x07\xd2" for frame in stack.sim.civ_frames_received
            )

            before = len(stack.sim.civ_frames_received)
            async with session.post(
                base + "/api/v1/commands",
                json={
                    "id": "mode-after-cold-poll",
                    "name": "set_mode",
                    "params": {"mode": "LSB", "filter": 2, "receiver": 0},
                },
            ) as response:
                body = await response.json()
                assert response.status == 200, body
                assert body["ok"] is True, body

            await _wait_until(lambda: _observed_mode_filter(stack) == ("LSB", 2))
            mode_state = await _get(session, base, "/api/v1/state")
            assert (mode_state["main"]["mode"], mode_state["main"]["filter"]) == (
                "LSB",
                2,
            )
            assert b"\x06\x00\x02" in [
                frame[4:-1] for frame in stack.sim.civ_frames_received[before:]
            ]

            before = len(stack.sim.civ_frames_received)
            assert await client.command(
                "set_filter", params={"filter": "FIL3", "receiver": 0}
            )
            await _wait_until(lambda: _observed_mode_filter(stack) == ("LSB", 3))
            filtered = await _get(session, base, "/api/v1/state")
            assert (filtered["main"]["mode"], filtered["main"]["filter"]) == (
                "LSB",
                3,
            )
            assert b"\x06\x00\x03" in [
                frame[4:-1] for frame in stack.sim.civ_frames_received[before:]
            ]
            observed = stack.radio.state_store.snapshot()
            assert observed.field("receiver.0.active.freq_mode.mode").value == "LSB"
            assert observed.field("receiver.0.active.freq_mode.filter_num").value == 3
    finally:
        await client.close()
        await stack.aclose()


async def _poll_active_and_check_sink(stack: soak_driver._SimulatorStack) -> None:
    radio = stack.radio
    tracker = radio._civ_request_tracker
    loop = asyncio.get_running_loop()
    retired: asyncio.Future[tuple[int | None, bool]] = loop.create_future()

    def observe_reply(raw: bytes) -> None:
        frame = parse_civ_frame(raw)
        if frame.command != 0x07 or frame.data not in (b"\xd2\x00", b"\xd2\x01"):
            return
        token = next(
            (
                waiter.token
                for waiter in tracker._ack_waiters
                if waiter.future is None
                and waiter.generation == tracker.generation
                and (key := waiter.response_key) is not None
                and key.command == 0x07
                and key.data_prefix == b"\xd2"
            ),
            None,
        )

        def after_route() -> None:
            if not retired.done():
                retired.set_result(
                    (
                        token,
                        all(waiter.token != token for waiter in tracker._ack_waiters),
                    )
                )

        loop.call_soon(after_route)

    subscription = radio.add_raw_civ_listener(observe_reply)
    try:
        query = next(
            query
            for query in build_state_queries(radio.profile)
            if query.command == 0x07 and query.data == b"\xd2"
        )
        assert stack.web._radio_poller is not None
        async with asyncio.timeout(10):
            while True:
                try:
                    await stack.web._radio_poller._send_one_state_query(query)
                except BackgroundSendDropped:
                    await asyncio.sleep(0.01)
                else:
                    break
            token, removed = await retired
        assert token is not None, "active poll reply had no keyed response sink"
        assert removed, "active poll reply left its response sink pending"
    finally:
        subscription.close()


@pytest.mark.parametrize("model", ["IC-7610", "IC-9700"])
async def test_poll_then_web_ptt_unkey_allows_rekey_without_fault(model: str) -> None:
    stack = await soak_driver._start_simulator_stack(
        managed_tx=True,
        model=model,
        dual_receiver=True,
        enable_audio=False,
        capture_civ_frames=True,
    )
    base = f"http://127.0.0.1:{stack.web_port}"
    client = soak_driver._TxSessionClient(base)
    try:
        assert stack.composition is not None
        await client.open()
        client.start_heartbeat()
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            for _ in range(2):
                await _poll_active_and_check_sink(stack)
                assert await client.command("ptt_on")
                await _wait_until(lambda: stack.sim.ptt_on)
                async with asyncio.timeout(10):
                    while True:
                        keyed = (await stack.composition.authority.snapshot()).state
                        if (
                            keyed.intent.kind is ManagedTxIntentKind.PTT
                            and keyed.pending_effect is None
                        ):
                            break
                        await asyncio.sleep(0.01)
                assert keyed.last_error is None
                await _poll_active_and_check_sink(stack)
                assert await client.command("ptt_off")
                async with asyncio.timeout(10):
                    while True:
                        state = (await stack.composition.authority.snapshot()).state
                        if (
                            state.intent.kind is ManagedTxIntentKind.RX
                            and not state.release_required
                            and state.pending_effect is None
                        ):
                            break
                        await asyncio.sleep(0.01)
                assert stack.sim.ptt_on is False
                assert state.last_error is None
                view = await _get(session, base, "/api/v1/managed-transmit")
                managed = view["managedTransmit"]
                assert managed["intent"] == {"kind": "rx"}
                assert managed["releaseRequired"] is False
                assert managed["lastError"] is None
                assert managed["lastActuation"]["result"] == "accepted"
            assert [on for _, on in stack.sim.ptt_transitions] == [True, False] * 2
    finally:
        await client.close()
        await stack.aclose()
