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
