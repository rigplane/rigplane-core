"""Web ``send_civ`` capture parity with the typed unkey (MOR-3384).

The typed unkey (``ptt_off`` / ``ptt`` with state False) never captures a
controlling seat: ``ControlHandler._validate_controller_command`` returns
before ``controller.capture()`` and ``CommandQueue._controller_ticket``
returns None for ``PttOff``.  A ``send_civ`` whose built frame ends a
transmission — PTT off (``1C 00 00``) or the voice TX memory stop
(``28 00 00``), bare or 0x29-wrapped, the set
``is_exact_raw_transmission_end_frame`` accepts — gets the same exemption
at both seats, so a client that is not the current controller — or holds
a remote ticket, whose typed PTT-off still works — can stop RF with
either form.  The raw tuner off (``1C 01 00``) stays captured, matching
the typed ``set_tuner_status`` 0, which ``_validate_controller_command``
captures too.  The poller's RF-state refusal keeps the wider de-key set
(``is_exact_raw_dekey_frame``, tuner off included): its tuner-off rows
in ``test_send_civ_exact_dekey_passes_rf_state_refusal``
(tests/test_radio_poller_tx_interlock.py) pass unchanged.  The rigctld
``w`` seat got the same narrow capture parity in PR #4134 (MOR-3380).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from test_controller_authority import Safety
from test_web_managed_tx_ingress import _Authority, _handler

from rigplane.runtime._poller_types import (
    CommandQueue,
    PttOff,
    SendCiv,
    validate_command_queue_entry_currency,
)
from rigplane.runtime.controller_authority import (
    ControllerAuthority,
    ControllerError,
    ControllerTicket,
)
from rigplane.runtime.tx_interlock import TxInterlockRefusal


_TRANSMISSION_END_PARAMS: tuple[tuple[dict[str, Any], str], ...] = (
    ({"command": 0x1C, "sub": 0x00, "data": "00"}, "ptt-off"),
    # ``send_civ`` expresses the 0x29-wrapped form as the receiver prefix
    # command with the inner actuation in ``data`` — the same parts the
    # poller rows in tests/test_radio_poller_tx_interlock.py queue.
    ({"command": 0x29, "sub": 0x00, "data": "1C0000"}, "cmd29-wrapped-ptt-off"),
    ({"command": 0x28, "sub": 0x00, "data": "00"}, "voice-memory-stop"),
    ({"command": 0x29, "sub": 0x00, "data": "280000"}, "cmd29-voice-memory-stop"),
)

_TRANSMISSION_END_SEND_CIV = (
    SendCiv(command=0x1C, sub=0x00, data=b"\x00"),
    SendCiv(command=0x29, sub=0x00, data=b"\x1c\x00\x00"),
    SendCiv(command=0x28, sub=0x00, data=b"\x00"),
    SendCiv(command=0x29, sub=0x00, data=b"\x28\x00\x00"),
)

_TUNER_OFF_PARAMS = {"command": 0x1C, "sub": 0x01, "data": "00"}


async def _remote_controller() -> tuple[Safety, ControllerAuthority, ControllerTicket]:
    safety = Safety()
    authority = safety.authority()
    await authority.set_mode("remote")
    grant = authority.acquire("browser")
    ticket = authority.attach(grant["controller_key"], "primary", "tab")
    return safety, authority, ticket


def _unmanaged_handler(
    authority: ControllerAuthority, *, read_only: bool = False
) -> tuple[CommandQueue, Any]:
    queue = CommandQueue()
    queue.bind_controller(authority)
    handler, _, radio = _handler(None, read_only=read_only, queue=queue)
    radio._controller_authority = authority
    radio.send_civ = AsyncMock()
    return queue, handler


async def _settle(authority: ControllerAuthority) -> None:
    authority.revoke()
    await authority.settle()


@pytest.mark.parametrize(
    "params",
    [p for p, _ in _TRANSMISSION_END_PARAMS],
    ids=[k for _, k in _TRANSMISSION_END_PARAMS],
)
async def test_send_civ_transmission_end_from_non_controller_reaches_queue(
    params: dict[str, Any],
) -> None:
    safety, authority, _ticket = await _remote_controller()
    try:
        queue, handler = _unmanaged_handler(authority)
        await handler._enqueue_command("send_civ", dict(params))
        [entry] = queue.drain_entries()
        assert entry.command == SendCiv(
            command=params["command"],
            sub=params["sub"],
            data=bytes.fromhex(params["data"]),
        )
        assert entry.controller_ticket is None
    finally:
        await _settle(authority)


@pytest.mark.parametrize(
    "params",
    [p for p, _ in _TRANSMISSION_END_PARAMS],
    ids=[k for _, k in _TRANSMISSION_END_PARAMS],
)
async def test_send_civ_transmission_end_with_remote_ticket_reaches_queue(
    params: dict[str, Any],
) -> None:
    safety, authority, ticket = await _remote_controller()
    try:
        queue, handler = _unmanaged_handler(authority)
        with authority.bind(ticket):
            await handler._enqueue_command("send_civ", dict(params))
        [entry] = queue.drain_entries()
        assert entry.controller_ticket is None
    finally:
        await _settle(authority)


async def test_send_civ_tuner_off_from_non_controller_is_captured() -> None:
    """A non-controller's raw tuner off captures, so the gate refuses it.

    The capture on an unbound session raises before any TX-family
    refusal list is consulted.
    """

    safety, authority, _ticket = await _remote_controller()
    try:
        queue, handler = _unmanaged_handler(authority)
        with pytest.raises(ControllerError, match="controller_invalid"):
            await handler._enqueue_command("send_civ", dict(_TUNER_OFF_PARAMS))
        assert queue.drain_entries() == []
    finally:
        await _settle(authority)


async def test_send_civ_tuner_off_with_remote_ticket_is_captured() -> None:
    safety, authority, ticket = await _remote_controller()
    try:
        queue, handler = _unmanaged_handler(authority)
        with (
            authority.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await handler._enqueue_command("send_civ", dict(_TUNER_OFF_PARAMS))
        assert queue.drain_entries() == []
    finally:
        await _settle(authority)


@pytest.mark.parametrize("cmd", _TRANSMISSION_END_SEND_CIV)
async def test_queue_gives_transmission_end_send_civ_no_ticket(cmd: SendCiv) -> None:
    """A transmission-ending ``SendCiv`` is accepted with no controlling seat.

    Mirrors the ``PttOff`` row of
    ``test_queue_captures_currency_even_when_local_and_safe_off_survives``
    in tests/test_controller_authority.py, which already pins the typed
    unkey's exemption: enqueue while a remote ticket is bound, revoke,
    and the entry must still pass currency validation.
    """
    safety = Safety()
    authority = safety.authority()
    queue = CommandQueue()
    queue.bind_controller(authority)
    await authority.set_mode("remote")
    grant = authority.acquire("browser")
    ticket = authority.attach(grant["controller_key"], "primary", "tab")
    with authority.bind(ticket):
        entry = queue.put_ordered(cmd)
    off = queue.put_ordered(PttOff())
    currency = dict(
        now=safety.now,
        provider_generation=None,
        connection_generation=None,
        session_is_live=lambda _: True,
    )
    authority.revoke()
    validate_command_queue_entry_currency(entry, **currency)
    validate_command_queue_entry_currency(off, **currency)
    assert entry.controller_ticket is None
    assert off.controller_ticket is None
    await authority.settle()


async def test_queue_takes_a_ticket_for_tuner_off_send_civ() -> None:
    safety = Safety()
    authority = safety.authority()
    queue = CommandQueue()
    queue.bind_controller(authority)
    await authority.set_mode("remote")
    grant = authority.acquire("browser")
    ticket = authority.attach(grant["controller_key"], "primary", "tab")
    with authority.bind(ticket):
        captured = queue.put_ordered(SendCiv(command=0x1C, sub=0x01, data=b"\x00"))
    assert captured.controller_ticket is not None
    await authority.settle()


async def test_queue_still_refuses_non_controller_non_transmission_end_send_civ() -> (
    None
):
    safety = Safety()
    authority = safety.authority()
    queue = CommandQueue()
    queue.bind_controller(authority)
    await authority.set_mode("remote")
    grant = authority.acquire("browser")
    ticket = authority.attach(grant["controller_key"], "primary", "tab")
    with pytest.raises(ControllerError):
        queue.put(SendCiv(command=0x1A, data=b"\x01"))
    with authority.bind(ticket):
        captured = queue.put_ordered(SendCiv(command=0x1C, sub=0x00, data=b"\x01"))
    assert captured.controller_ticket is not None
    currency = dict(
        now=safety.now,
        provider_generation=None,
        connection_generation=None,
        session_is_live=lambda _: True,
    )
    authority.revoke()
    with pytest.raises(ControllerError):
        validate_command_queue_entry_currency(captured, **currency)
    await authority.settle()


async def test_remote_ticket_send_civ_ptt_on_still_refused() -> None:
    safety, authority, ticket = await _remote_controller()
    try:
        _, handler = _unmanaged_handler(authority)
        with (
            authority.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await handler._enqueue_command(
                "send_civ", {"command": 0x1C, "sub": 0x00, "data": "01"}
            )
    finally:
        await _settle(authority)


async def test_non_controller_non_exact_transmission_end_still_refused() -> None:
    safety, authority, _ticket = await _remote_controller()
    try:
        _, handler = _unmanaged_handler(authority)
        with pytest.raises(ControllerError):
            await handler._enqueue_command(
                "send_civ", {"command": 0x1C, "sub": 0x00, "data": "0000"}
            )
    finally:
        await _settle(authority)


async def test_remote_ticket_non_tx_send_civ_still_refused() -> None:
    """A remote ticket refuses every ``send_civ``, whatever the frame.

    The remote-ticket refusal list in
    ``ControlHandler._validate_controller_command`` is name-based, so a
    frequency-read frame is refused exactly like a TX-family one.
    """
    safety, authority, ticket = await _remote_controller()
    try:
        _, handler = _unmanaged_handler(authority)
        with (
            authority.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await handler._enqueue_command("send_civ", {"command": 0x03})
    finally:
        await _settle(authority)


async def test_remote_ticket_malformed_send_civ_still_refused() -> None:
    safety, authority, ticket = await _remote_controller()
    try:
        _, handler = _unmanaged_handler(authority)
        with (
            authority.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await handler._enqueue_command("send_civ", {"command": "0xZZ"})
    finally:
        await _settle(authority)


async def test_local_malformed_send_civ_keeps_param_error() -> None:
    authority = Safety().authority()
    queue, handler = _unmanaged_handler(authority)
    with pytest.raises(ValueError, match="even-length hex string"):
        await handler._enqueue_command(
            "send_civ", {"command": 0x1C, "sub": 0x00, "data": "0"}
        )
    assert queue.drain_entries() == []


async def test_read_only_send_civ_transmission_end_still_refused() -> None:
    """``send_civ`` stays in ``ControlHandler._TX_COMMANDS``: a read-only
    client is refused exactly as before, transmission end or not."""
    authority = Safety().authority()
    queue, handler = _unmanaged_handler(authority, read_only=True)
    with pytest.raises(PermissionError, match="read-only mode: send_civ rejected"):
        await handler._enqueue_command(
            "send_civ", {"command": 0x1C, "sub": 0x00, "data": "00"}
        )
    assert queue.drain_entries() == []


async def test_managed_non_controller_transmission_end_still_refused() -> None:
    """Under managed TX authority the raw PTT off stays refused.

    With the controller gate skipped for the transmission end, the
    refusal that now fires is the managed TX-family one from
    ``ControlHandler._enqueue_rc_frequency``.
    """
    safety, authority, _ticket = await _remote_controller()
    try:
        queue = CommandQueue()
        queue.bind_controller(authority)
        handler, _, radio = _handler(_Authority(), queue=queue)
        radio._controller_authority = authority
        radio.send_civ = AsyncMock()
        with pytest.raises(TxInterlockRefusal) as excinfo:
            await handler._enqueue_command(
                "send_civ", {"command": 0x1C, "sub": 0x00, "data": "00"}
            )
        assert excinfo.value.reason_code == "managed_tx_refused"
        assert queue.drain_entries() == []
    finally:
        await _settle(authority)
