"""Web disconnect pressed while the radio is reconnecting.

The operator's disconnect must stop a reconnect in progress, including during
the lifecycle recovery's backoff wait between attempts, where ``conn_state``
reads "disconnected" while ``_run_recover`` still owns another attempt.

MOR-3777: disconnect is REST-only (``POST /api/v1/radio/disconnect``); the
WebSocket ``radio_disconnect`` message these tests used to drive is gone.
The REST path always takes the owner-level ``radio.disconnect()`` (which
cancels the recovery and bumps the owner-stop epoch), advances the shared
store's provider generation, and marks the operator disconnect.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from test_web_server_coverage import _FakeWriter, _response_json


async def _rest_disconnect(server: WebServer) -> tuple[int, dict]:
    writer = _FakeWriter()
    await server._handle_radio_control("/api/v1/radio/disconnect", writer)  # type: ignore[arg-type]
    return _response_json(writer)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        RadioConnectionState.RECONNECTING,
        RadioConnectionState.CONNECTING,
        RadioConnectionState.DISCONNECTED,
        RadioConnectionState.CONNECTED,
    ],
)
async def test_rest_disconnect_takes_owner_teardown_in_every_state(
    state: RadioConnectionState,
) -> None:
    radio = SimpleNamespace(
        capabilities=set(),
        connected=state is RadioConnectionState.CONNECTED,
        conn_state=state,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
    )
    server = WebServer(radio, WebConfig(port=0))  # type: ignore[arg-type]
    generation = server.command_state_store.provider_generation

    status, body = await _rest_disconnect(server)

    assert status == 200
    assert body == {"status": "disconnected"}
    radio.disconnect.assert_awaited_once()  # owner teardown stops any loop
    radio.soft_disconnect.assert_not_awaited()
    assert server.command_state_store.provider_generation == generation + 1
    assert server._operator_disconnected is True


@pytest.mark.asyncio
async def test_disconnect_during_recovery_backoff_stops_further_attempts() -> None:
    """A real IcomRadio mid-recovery: conn_state reads DISCONNECTED in the wait.

    Control discovery times out, the lifecycle recovery
    (``CoreRadioSessionLifecycle._run_recover`` via ``radio.soft_reconnect()``)
    fails its first attempt and sleeps the inter-attempt wait — here the
    lifecycle's own ``recovery_backoff_s`` knob shortened from the 45 s / 60 s
    production defaults — while ``conn_state`` reads DISCONNECTED. The REST
    disconnect must still stop the recovery: reply "disconnected", the
    recovery task ends, the provider epoch advances, and no further connect
    attempt fires within a window longer than the backoff.
    """
    radio = IcomRadio("127.0.0.1", model="IC-7610", timeout=0.5)
    lifecycle = radio._session_lifecycle
    lifecycle._max_recovery_attempts = 3
    lifecycle._recovery_backoff_s = (0.5, 0.5)
    attempts = 0

    async def _discovery_timeout() -> None:
        nonlocal attempts
        attempts += 1
        # Mirror ControlPhaseRuntime._connect_once on discovery timeout:
        # the attempt marks CONNECTING, then releases back to DISCONNECTED.
        radio._conn_state = RadioConnectionState.CONNECTING
        radio._conn_state = RadioConnectionState.DISCONNECTED
        raise TimeoutError("control discovery timed out")

    server = WebServer(radio, WebConfig(port=0))
    generation = server.command_state_store.provider_generation
    with patch.object(radio._control_phase, "_connect_once", _discovery_timeout):
        recovery = asyncio.create_task(radio.soft_reconnect())
        try:
            for _ in range(250):
                if (
                    attempts == 1
                    and radio.conn_state is RadioConnectionState.DISCONNECTED
                    and not radio.connected
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("recovery did not reach the inter-attempt wait")
            status, body = await _rest_disconnect(server)
            attempts_at_handler = attempts  # 1: first attempt failed, waiting
            assert status == 200  # 2: reply
            assert body == {"status": "disconnected"}
            # 3: provider epoch invalidated (the real radio's teardown may
            # advance it again on its own; it must at least move once)
            assert server.command_state_store.provider_generation > generation
            await asyncio.sleep(0.7)  # longer than the 0.5 s inter-attempt wait
            assert attempts == attempts_at_handler  # 4: no further attempt
            assert recovery.done()  # 5: the recovery task ended
            assert lifecycle._recover_task is None  # 6: lifecycle owns nothing
        finally:
            if not recovery.done():
                recovery.cancel()
                with suppress(asyncio.CancelledError):
                    await recovery
