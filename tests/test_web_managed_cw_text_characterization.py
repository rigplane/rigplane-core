"""Characterization of the typed CW text commands in managed TX mode.

A managed TX authority is installed (managed mode) and does not hold TX
(intent ``rx``), observed PTT is False, and the WebSocket client is local
and non-read-only. Pinned behaviour of the typed commands dispatched
through ``ControlHandler``:

* ``send_cw_text`` is served by ``ControlHandler._ro_send_cw_text``, which
  awaits ``radio.send_cw_text(text)`` directly: no authority submission, no
  force-off, no observed-RF gate, no command queue.
* ``stop_cw_text`` likewise awaits ``radio.stop_cw_text()`` directly.

The desired default — refuse ``send_cw_text`` unless the authority holds
TX — is encoded as a strict xfail below.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.web.protocol import decode_json
from test_web_managed_tx_ingress import _Authority, _handler


@pytest.mark.asyncio
async def test_characterization_managed_send_cw_text_keys_rig_with_authority_not_holding_tx() -> None:
    """Characterization: managed send_cw_text keys the rig unattended.

    A local, non-read-only WebSocket client sends the typed ``send_cw_text``
    command while the managed TX authority is installed and idle (intent
    ``rx``) and observed PTT is False. Today the command is acknowledged and
    reaches ``radio.send_cw_text`` directly: the authority records no
    submission and no force-off, and the command queue is bypassed. On a real
    rig this command keys the transmitter for the text (break-in); the fake
    radio here cannot transmit, so keying is observable only as the awaited
    ``radio.send_cw_text`` call — no claim is made about break-in timing.
    """
    authority = _Authority()
    ws = SimpleNamespace(send_text=AsyncMock())
    handler, server, radio = _handler(authority, ws=ws, observed=False)
    radio.send_cw_text = AsyncMock()

    await handler._dispatch_command("cmd-cw", "send_cw_text", {"text": "cq"})

    response = decode_json(ws.send_text.await_args.args[0])
    assert response["ok"] is True
    assert response["result"] == {"text": "cq"}
    radio.send_cw_text.assert_awaited_once_with("cq")
    server.command_queue.put.assert_not_called()
    assert authority.calls == []
    assert authority.force_off_calls == 0
    assert authority.intent == "rx"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="MOR-3382: managed send_cw_text keys the rig without the managed TX authority",
)
@pytest.mark.asyncio
async def test_managed_send_cw_text_refused_while_authority_does_not_hold_tx() -> None:
    """Desired default: managed mode refuses send_cw_text without TX hold.

    Asserts the refusal shape recorded as the ticket default: with the
    authority not holding TX, the command must fail the client and
    ``radio.send_cw_text`` must not be awaited. This is not today's
    behaviour (see the characterization test above), so the assertions fail
    and the test xfails strictly.
    """
    authority = _Authority()
    ws = SimpleNamespace(send_text=AsyncMock())
    handler, _, radio = _handler(authority, ws=ws, observed=False)
    radio.send_cw_text = AsyncMock()

    await handler._dispatch_command("cmd-cw", "send_cw_text", {"text": "cq"})

    response = decode_json(ws.send_text.await_args.args[0])
    assert response["ok"] is False
    radio.send_cw_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_stop_cw_text_reaches_radio_with_authority_not_holding_tx() -> None:
    """Managed stop_cw_text reaches the radio while the authority holds no TX.

    ``stop_cw_text`` is the release path: this pin requires it to reach
    ``radio.stop_cw_text`` in managed mode even while the authority does not
    hold TX, so it must keep passing under every shape of the send_cw_text
    guard. Stopping is not keying; the fake records the awaited call only.
    """
    authority = _Authority()
    ws = SimpleNamespace(send_text=AsyncMock())
    handler, _, radio = _handler(authority, ws=ws, observed=False)
    radio.stop_cw_text = AsyncMock()

    await handler._dispatch_command("cmd-cw", "stop_cw_text", {})

    response = decode_json(ws.send_text.await_args.args[0])
    assert response["ok"] is True
    radio.stop_cw_text.assert_awaited_once_with()
