"""Web disconnect pressed while the radio is reconnecting.

``ControlHandler._handle_radio_disconnect`` used to answer
``already_disconnected`` whenever ``radio.connected`` was False — including
while the backend was in the middle of reconnecting, so the operator's
disconnect was a no-op and the reconnect kept logging back in. Worse, during
the lifecycle recovery's backoff wait between attempts ``conn_state`` reads
"disconnected" while ``_run_recover`` still owns another attempt, so matching
only "connecting"/"reconnecting" still misses it. These tests pin the fixed
contract: while not connected and no teardown is already in flight
("disconnecting"), the handler takes the owner-level ``radio.disconnect()``
path (which cancels the recovery and bumps the owner-stop epoch) plus the
same provider-generation invalidation as the HTTP disconnect path; a radio
already tearing down stays a no-op and a connected radio keeps the
``soft_disconnect`` path.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio
from rigplane.web.handlers import ControlHandler
from rigplane.web.protocol import decode_json


def _handler(
    ws: SimpleNamespace,
    radio: object,
    server: object = None,
) -> ControlHandler:
    return ControlHandler(
        ws,
        radio,
        "9.9.9",
        "IC-7610",
        server=server,
        session_id=None,
        managed_tx_authority=None,
    )


class _ProviderStore:
    """Counting stand-in for the server's shared state store."""

    def __init__(self) -> None:
        self.generations = 0

    def begin_provider_generation(self) -> int:
        self.generations += 1
        return self.generations


def _sent(ws: SimpleNamespace) -> list[dict[str, object]]:
    return [decode_json(call.args[0]) for call in ws.send_text.await_args_list]


@pytest.mark.asyncio
async def test_disconnect_during_reconnecting_stops_the_reconnect() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=False,
        conn_state=RadioConnectionState.RECONNECTING,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
    )
    handler = _handler(ws, radio)

    await handler._handle_radio_disconnect({"id": "r1"})

    radio.disconnect.assert_awaited_once()  # 1: owner teardown stops the loop
    radio.soft_disconnect.assert_not_awaited()  # 2: no soft path off CONNECTED
    msgs = _sent(ws)
    assert msgs[-2]["result"] == {"status": "disconnected"}  # 3: reply status
    assert msgs[-1]["event"] == "connection_state"  # 4: one state broadcast
    assert msgs[-1]["connected"] is False  # 5: broadcast says disconnected


@pytest.mark.asyncio
async def test_disconnect_during_connecting_stops_the_reconnect() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=False,
        conn_state=RadioConnectionState.CONNECTING,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
    )
    handler = _handler(ws, radio)

    await handler._handle_radio_disconnect({"id": "c1"})

    radio.disconnect.assert_awaited_once()  # 1: owner teardown stops the loop
    radio.soft_disconnect.assert_not_awaited()  # 2: no soft path off CONNECTED
    msgs = _sent(ws)
    assert msgs[-2]["result"] == {"status": "disconnected"}  # 3: reply status
    assert msgs[-1]["event"] == "connection_state"  # 4: one state broadcast
    assert msgs[-1]["connected"] is False  # 5: broadcast says disconnected


@pytest.mark.asyncio
async def test_disconnect_when_truly_disconnected_tears_down_and_replies_disconnected() -> (
    None
):
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=False,
        conn_state=RadioConnectionState.DISCONNECTED,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
    )
    handler = _handler(ws, radio)

    await handler._handle_radio_disconnect({"id": "d1"})

    radio.disconnect.assert_awaited_once()  # 1: idempotent owner teardown
    radio.soft_disconnect.assert_not_awaited()  # 2: no soft path off CONNECTED
    msgs = _sent(ws)
    assert msgs[-2]["result"] == {"status": "disconnected"}  # 3: reply status
    assert msgs[-1]["event"] == "connection_state"  # 4: one state broadcast
    assert msgs[-1]["connected"] is False  # 5: broadcast says disconnected


@pytest.mark.asyncio
async def test_disconnect_invalidates_provider_generation_like_http_path() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    store = _ProviderStore()
    radio = SimpleNamespace(
        connected=False,
        conn_state=RadioConnectionState.DISCONNECTED,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
    )
    handler = _handler(ws, radio, server=SimpleNamespace(command_state_store=store))

    await handler._handle_radio_disconnect({"id": "g1"})

    radio.disconnect.assert_awaited_once()  # 1: owner teardown runs first
    assert store.generations == 1  # 2: same epoch invalidation as HTTP disconnect
    msgs = _sent(ws)
    assert msgs[-2]["result"] == {"status": "disconnected"}  # 3: reply status
    assert msgs[-1]["event"] == "connection_state"  # 4: one state broadcast


@pytest.mark.asyncio
async def test_disconnect_when_connected_keeps_soft_disconnect() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=True,
        conn_state=RadioConnectionState.CONNECTED,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
        soft_reconnect=AsyncMock(),
    )
    handler = _handler(ws, radio)

    await handler._handle_radio_disconnect({"id": "d2"})

    radio.soft_disconnect.assert_awaited_once()  # 1: fast-reconnect path kept
    radio.disconnect.assert_not_awaited()  # 2: no owner teardown on CONNECTED
    msgs = _sent(ws)
    assert msgs[-2]["result"] == {"status": "disconnected"}  # 3: reply status
    assert msgs[-1]["event"] == "connection_state"  # 4: one state broadcast
    assert msgs[-1]["connected"] is False  # 5: broadcast says disconnected


@pytest.mark.asyncio
async def test_disconnect_during_disconnecting_is_a_noop() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=False,
        conn_state=RadioConnectionState.DISCONNECTING,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
    )
    handler = _handler(ws, radio)

    await handler._handle_radio_disconnect({"id": "d3"})

    radio.disconnect.assert_not_awaited()  # 1: teardown already in flight
    radio.soft_disconnect.assert_not_awaited()  # 2: nothing extra to tear down
    msgs = _sent(ws)
    assert msgs[-1]["result"] == {"status": "already_disconnected"}  # 3: noop reply
    assert len(msgs) == 1  # 4: no state broadcast for a noop


@pytest.mark.asyncio
async def test_disconnect_during_recovery_backoff_stops_further_attempts() -> None:
    """A real IcomRadio mid-recovery: conn_state reads DISCONNECTED in the wait.

    Mirrors the reviewer's repro: control discovery times out, the lifecycle
    recovery (``CoreRadioSessionLifecycle._run_recover`` via
    ``radio.soft_reconnect()``) fails its first attempt and sleeps the
    inter-attempt wait — here the lifecycle's own ``recovery_backoff_s``
    knob shortened from the 45 s / 60 s production defaults — while
    ``conn_state`` reads DISCONNECTED. The handler must still stop the
    recovery: reply "disconnected", the recovery task ends, the provider
    epoch advances, and no further connect attempt fires within a window
    longer than the backoff.
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

    store = _ProviderStore()
    ws = SimpleNamespace(send_text=AsyncMock())
    handler = _handler(ws, radio, server=SimpleNamespace(command_state_store=store))
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
            await handler._handle_radio_disconnect({"id": "r-backoff"})
            attempts_at_handler = attempts  # 1: first attempt failed, waiting
            msgs = _sent(ws)
            assert msgs[-2]["result"] == {"status": "disconnected"}  # 2: reply
            assert msgs[-1]["event"] == "connection_state"  # 3: broadcast
            assert msgs[-1]["connected"] is False
            assert store.generations == 1  # 4: provider epoch invalidated
            await asyncio.sleep(0.7)  # longer than the 0.5 s inter-attempt wait
            assert attempts == attempts_at_handler  # 5: no further attempt
            assert recovery.done()  # 6: the recovery task ended
            assert lifecycle._recover_task is None  # 7: lifecycle owns nothing
        finally:
            if not recovery.done():
                recovery.cancel()
                with suppress(asyncio.CancelledError):
                    await recovery
