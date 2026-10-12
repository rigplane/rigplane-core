"""Rigctld raw ``w`` de-key parity with the typed ``T 0`` at the capture gate.

A rigctld client that is not the current controller can unkey with the
typed ``T 0`` (``RigctldHandler.execute`` skips ``controller.capture()``
for it) but, before this change, an exact raw de-key sent through ``w``
was refused with ``EACCESS`` at the same gate.  These rows pin the
parity: an exact raw de-key frame reaches the radio's raw send with the
controller held by another client, while every other raw payload still
requires the controller seat.  The read-only gate and the managed-mode
TX-family refusal are unchanged and refuse the raw de-key exactly as
they refuse ``T 0``.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from test_controller_authority import Safety
from test_rigctld_handler import make_mock_radio
from test_rigctld_managed_context import _AuthorityTouchProbe, _DefaultExecutor

from rigplane.core.command_service import CommandService
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.rigctld.contract import HamlibError, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import parse_line
from rigplane.runtime._poller_types import CommandQueue


def _rx_store() -> StateStore:
    """Fresh-known-RX store so only the controller gate decides these rows."""
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=time.monotonic(),
            max_age=1e9,
        )
    )
    return store


@pytest.fixture
async def held_controller():
    """``ControllerAuthority`` in remote mode, lease held by another client.

    The rigctld client under test binds no ticket, so every
    ``capture()`` on this authority raises ``ControllerError`` — the
    same installation ``test_web_controller`` uses for its rigctld
    safety-off row.
    """
    safety = Safety()
    authority = safety.authority()
    await authority.set_mode("remote")
    grant = authority.acquire("pro")
    authority.attach(grant["controller_key"], "primary", "native")
    try:
        yield authority
    finally:
        await authority.release(grant["controller_key"])


def _unmanaged_handler(authority, **config_kwargs):
    radio = make_mock_radio()
    radio._controller_authority = authority
    radio._send_civ_raw = AsyncMock(return_value=None)
    store = _rx_store()
    handler = RigctldHandler(radio, RigctldConfig(**config_kwargs), state_store=store)
    return handler, radio


def _managed_handler(authority):
    radio = make_mock_radio()
    radio._controller_authority = authority
    radio._send_civ_raw = AsyncMock(return_value=None)
    store = _rx_store()
    handler = RigctldHandler(
        radio,
        RigctldConfig(),
        state_store=store,
        managed_tx_authority=_AuthorityTouchProbe(),
        command_queue=CommandQueue(),
        command_service=CommandService(executor=_DefaultExecutor(), state_store=store),
    )
    return handler, radio


_DEKEY_WIRES = (
    (b"w FE FE 98 E0 1C 00 00 FD", b"\xfe\xfe\x98\xe0\x1c\x00\x00\xfd"),
    (b"w FE FE 98 E0 1C 01 00 FD", b"\xfe\xfe\x98\xe0\x1c\x01\x00\xfd"),
    (
        b"w FE FE 98 E0 29 00 1C 00 00 FD",
        b"\xfe\xfe\x98\xe0\x29\x00\x1c\x00\x00\xfd",
    ),
)


@pytest.mark.parametrize(
    ("wire", "frame"), _DEKEY_WIRES, ids=["ptt-off", "tuner-off", "cmd29-ptt-off"]
)
async def test_unmanaged_raw_dekey_skips_controller_capture(
    held_controller, wire, frame
):
    handler, radio = _unmanaged_handler(held_controller)

    response = await handler.execute(parse_line(wire))

    assert response.ok, (
        "an exact raw de-key must reach the radio without holding the "
        "controller seat, exactly as the typed T 0 does"
    )
    radio._send_civ_raw.assert_awaited_once_with(frame)


@pytest.mark.parametrize(
    "wire",
    (
        b"w FE FE 98 E0 1C 00 01 FD",
        b"w FE FE 98 E0 03 FD",
        b"w FE FE 98 E0 1C 00 00 00 FD",
        b"w FE ZZ FD",
    ),
    ids=["raw-ptt-on", "non-tx-raw", "non-exact-dekey", "unparseable-hex"],
)
async def test_unmanaged_non_dekey_raw_still_requires_the_controller_seat(
    held_controller, wire
):
    handler, radio = _unmanaged_handler(held_controller)

    response = await handler.execute(parse_line(wire))

    assert response.error is HamlibError.EACCESS
    radio._send_civ_raw.assert_not_awaited()


@pytest.mark.parametrize(
    "wire", [b"T 0", b"w FE FE 98 E0 1C 00 00 FD"], ids=["typed-unkey", "raw-dekey"]
)
async def test_read_only_still_refuses_both_unkeys(held_controller, wire):
    handler, radio = _unmanaged_handler(held_controller, read_only=True)

    response = await handler.execute(parse_line(wire))

    assert response.error is HamlibError.EACCESS
    radio._send_civ_raw.assert_not_awaited()
    radio.set_ptt.assert_not_awaited()


async def test_managed_non_controller_raw_dekey_is_refused_by_payload(
    held_controller,
):
    handler, radio = _managed_handler(held_controller)

    response = await handler.execute(parse_line(b"w FE FE 98 E0 1C 00 00 FD"))

    assert response.error is HamlibError.ERJCTED
    radio._send_civ_raw.assert_not_awaited()
