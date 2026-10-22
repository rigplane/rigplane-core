"""Characterization of the typed CW text commands in managed TX mode.

A managed TX authority is installed (managed mode) and does not hold TX
(intent ``rx``), observed PTT is False, and the WebSocket client is
non-read-only. Pinned behaviour of the typed commands dispatched
through ``ControlHandler``:

* ``send_cw_text`` is served by ``ControlHandler._ro_send_cw_text``, which
  awaits ``radio.send_cw_text(text)`` with no managed-authority admission:
  the authority records no submission and no force-off, and nothing is
  enqueued on the command queue. This is the accepted behaviour by owner
  decision (MOR-3382, 2026-10-12): typed ``send_cw_text`` keys the rig
  without managed-authority admission because its frames run through the
  abort fence that a managed force-off cancels.
* ``stop_cw_text`` likewise awaits ``radio.stop_cw_text()`` directly.

The abort-fence half is proved by
``tests/test_fenced_local_tx_providers.py``:
``test_icom_force_off_suppresses_late_cw_write_and_remaining_chunks``
starts a long ``send_cw_text`` under the managed composition, forces the
composition's abort fence off, and shows the in-flight send ends while
later chunks never reach the fake wire.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import CommandQueue
from test_web_managed_tx_ingress import _Authority, _handler


@pytest.mark.asyncio
async def test_characterization_managed_send_cw_text_keys_rig_with_authority_not_holding_tx() -> (
    None
):
    """Characterization: managed send_cw_text keys the rig unadmitted.

    A non-read-only WebSocket client sends the typed ``send_cw_text``
    command while the managed TX authority is installed and idle (intent
    ``rx``) and observed PTT is False. The command is acknowledged and
    awaits ``radio.send_cw_text`` with no managed-authority admission:
    the authority records no submission and no force-off, and nothing is
    enqueued on the command queue. This is the accepted behaviour by
    owner decision (MOR-3382, 2026-10-12): typed ``send_cw_text`` keys
    the rig without managed-authority admission because its frames run
    through the abort fence that a managed force-off cancels.
    """
    authority = _Authority()
    ws = SimpleNamespace(send_text=AsyncMock())
    queue = CommandQueue()
    handler, _, radio = _handler(authority, ws=ws, observed=False, queue=queue)
    radio.send_cw_text = AsyncMock()

    await handler._dispatch_command("cmd-cw", "send_cw_text", {"text": "cq"})

    response = decode_json(ws.send_text.await_args.args[0])
    assert response["ok"] is True
    assert response["result"] == {"text": "cq"}
    radio.send_cw_text.assert_awaited_once_with("cq")
    assert queue.drain_entries() == []
    assert authority.calls == []
    assert authority.force_off_calls == 0
    assert authority.intent == "rx"


@pytest.mark.asyncio
async def test_managed_stop_cw_text_reaches_radio_with_authority_not_holding_tx() -> (
    None
):
    """Managed stop_cw_text reaches the radio while the authority holds no TX.

    The typed ``stop_cw_text`` command is acknowledged and awaits
    ``radio.stop_cw_text()``.
    """
    authority = _Authority()
    ws = SimpleNamespace(send_text=AsyncMock())
    handler, _, radio = _handler(authority, ws=ws, observed=False)
    radio.stop_cw_text = AsyncMock()

    await handler._dispatch_command("cmd-cw", "stop_cw_text", {})

    response = decode_json(ws.send_text.await_args.args[0])
    assert response["ok"] is True
    radio.stop_cw_text.assert_awaited_once_with()
