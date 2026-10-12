"""Web disconnect pressed while the radio is reconnecting.

``ControlHandler._handle_radio_disconnect`` used to answer
``already_disconnected`` whenever ``radio.connected`` was False — including
while the backend was in the middle of reconnecting (``conn_state`` is
"connecting"/"reconnecting"), so the operator's disconnect was a no-op and
the reconnect loop logged back in. These tests pin the fixed contract: a
disconnect during "connecting"/"reconnecting" takes the owner-level
``radio.disconnect()`` path (which stops the reconnect loop), while a truly
disconnected radio and a radio already tearing down ("disconnecting") stay a
no-op and a connected radio keeps the ``soft_disconnect`` path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.web.handlers import ControlHandler
from rigplane.web.protocol import decode_json


def _handler(
    ws: SimpleNamespace,
    radio: object,
) -> ControlHandler:
    return ControlHandler(
        ws,
        radio,
        "9.9.9",
        "IC-7610",
        server=None,
        session_id=None,
        managed_tx_authority=None,
    )


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
async def test_disconnect_when_truly_disconnected_is_a_noop() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=False,
        conn_state=RadioConnectionState.DISCONNECTED,
        disconnect=AsyncMock(),
        soft_disconnect=AsyncMock(),
    )
    handler = _handler(ws, radio)

    await handler._handle_radio_disconnect({"id": "d1"})

    radio.disconnect.assert_not_awaited()  # 1: nothing to stop
    radio.soft_disconnect.assert_not_awaited()  # 2: nothing to tear down
    msgs = _sent(ws)
    assert msgs[-1]["result"] == {"status": "already_disconnected"}  # 3: noop reply
    assert len(msgs) == 1  # 4: no state broadcast for a noop


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
