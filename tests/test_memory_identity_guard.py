"""Memory mutations must respect the connect-time identity hold."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from unittest.mock import patch

import pytest

from rigplane.exceptions import ConnectionError
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.transport import ConnectionState
from test_icom_lan_identity_gate import (
    _ColdConnectPatches,
    _cold_radio,
    _cold_teardown,
    _identity_sends,
    _wait_until,
)
from test_memory_write_layouts import _mem


@pytest.mark.parametrize("model", ["IC-705", "IC-7610"])
@pytest.mark.parametrize("operation", ["clear", "write"])
@pytest.mark.parametrize("reconnect", [False, True], ids=["connect", "reconnect"])
async def test_memory_mutation_refused_while_identity_read_pending(
    model: str, operation: str, reconnect: bool
) -> None:
    def make_model(*args: object, **kwargs: object) -> IcomRadio:
        kwargs["model"] = model
        return IcomRadio(*args, **kwargs)

    with patch("test_icom_lan_identity_gate.IcomRadio", side_effect=make_model):
        radio, ctrl, civ = _cold_radio(expected_ids=("0B90",), timeout=1.0)

    if reconnect:
        radio._civ_transport = None
        ctrl._udp_transport = object()
        ctrl.state = ConnectionState.CONNECTED
    with _ColdConnectPatches(radio, civ):
        task = asyncio.create_task(
            radio._control_phase.soft_reconnect()
            if reconnect
            else radio._control_phase._connect_once()
        )
        try:
            assert await _wait_until(lambda: _identity_sends(civ) == 1)
            assert radio._conn_state is RadioConnectionState.CONNECTED
            assert radio._lan_identity_gate_running
            before = list(civ.sent_packets)
            mem = _mem(model, duplex=0)
            with pytest.raises(ConnectionError, match="identity hold"):
                if operation == "clear":
                    await radio.memory_clear(mem.channel, mem.group)
                else:
                    await radio.set_memory_contents(mem)
            assert civ.sent_packets == before
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await _cold_teardown(radio)


@pytest.mark.parametrize("model", ["IC-705", "IC-7610"])
@pytest.mark.parametrize("operation", ["clear", "write"])
async def test_memory_mutation_rechecks_identity_after_waiting_for_lock(
    model: str, operation: str
) -> None:
    def make_model(*args: object, **kwargs: object) -> IcomRadio:
        kwargs["model"] = model
        return IcomRadio(*args, **kwargs)

    with patch("test_icom_lan_identity_gate.IcomRadio", side_effect=make_model):
        radio, ctrl, civ = _cold_radio(expected_ids=("0B90",), timeout=1.0)
    ctrl._udp_transport = object()
    ctrl.state = ConnectionState.CONNECTED
    radio._civ_transport = civ
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._check_connected()
    mem = _mem(model, duplex=0)
    await radio._memory_select_lock.acquire()
    lock_owned = True
    mutation = asyncio.create_task(
        radio.memory_clear(mem.channel, mem.group)
        if operation == "clear"
        else radio.set_memory_contents(mem)
    )
    reconnect = None
    with _ColdConnectPatches(radio, civ):
        try:
            assert await _wait_until(lambda: bool(radio._memory_select_lock._waiters))
            radio._civ_transport = None
            reconnect = asyncio.create_task(radio._control_phase.soft_reconnect())
            assert await _wait_until(lambda: _identity_sends(civ) == 1)
            assert radio._lan_identity_gate_running
            before = list(civ.sent_packets)
            radio._memory_select_lock.release()
            lock_owned = False
            with pytest.raises(ConnectionError, match="identity hold"):
                await mutation
            assert civ.sent_packets == before
        finally:
            if lock_owned:
                radio._memory_select_lock.release()
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)
            if reconnect is not None:
                reconnect.cancel()
                with suppress(asyncio.CancelledError):
                    await reconnect
            await _cold_teardown(radio)
