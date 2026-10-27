"""A released LAN control session requires discovery before another login."""

from __future__ import annotations

import asyncio

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect


@pytest.mark.parametrize("model, address", [("IC-7610", 0x98), ("IC-705", 0xA4)])
async def test_connect_disconnect_connect_repeats_full_handshake(
    model: str, address: int
) -> None:
    sim = MockIcomRadio(
        radio_addr=address,
        single_owner=True,
        keepalive_hold_s=5.0,
        require_control_discovery=True,
    )
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model=model,
        timeout=0.5,
    )
    try:
        with fast_connect():
            await asyncio.wait_for(radio.connect(), 10.0)
            assert await radio.get_freq() == 14_074_000
            assert sim.login_count == 1
            await radio.disconnect()
            assert radio.conn_state is RadioConnectionState.DISCONNECTED
            await asyncio.wait_for(radio.connect(), 10.0)
            assert radio.conn_state is RadioConnectionState.CONNECTED
            assert sim.login_count == 2
            assert sim.session_held
            assert await radio.get_freq() == 14_074_000
    finally:
        await radio.disconnect()
        await sim.stop()


async def test_cancelled_disconnect_after_civ_retirement_still_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling disconnect during the worker join skips no teardown step."""
    sim = MockIcomRadio(
        single_owner=True, keepalive_hold_s=5.0, require_control_discovery=True
    )
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
        timeout=0.5,
    )
    try:
        with fast_connect():
            await asyncio.wait_for(radio.connect(), 10.0)
            commander = radio._commander
            assert commander is not None
            worker = commander._worker
            assert worker is not None
            civ_rx_task = radio._civ_rx_task
            assert civ_rx_task is not None
            ctrl = radio._ctrl_transport

            # CI-V transport already retired, as after managed-TX retirement.
            assert radio._civ_transport is not None
            await radio._civ_transport.disconnect()
            radio._civ_transport = None

            real_stop = type(commander).stop
            entered = asyncio.Event()

            async def slow_stop(self: object) -> None:
                entered.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    await real_stop(self)  # type: ignore[arg-type]

            monkeypatch.setattr(type(commander), "stop", slow_stop)
            task = asyncio.create_task(radio.disconnect())
            await asyncio.wait_for(entered.wait(), 5.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10.0)
            monkeypatch.undo()

            assert worker.done()
            assert civ_rx_task.done()
            assert ctrl._udp_transport is None
            assert not sim.session_held
            assert radio.conn_state is RadioConnectionState.DISCONNECTED

            await asyncio.wait_for(radio.connect(), 10.0)
            assert radio.conn_state is RadioConnectionState.CONNECTED
            assert await radio.get_freq() == 14_074_000
    finally:
        await radio.disconnect()
        await sim.stop()
