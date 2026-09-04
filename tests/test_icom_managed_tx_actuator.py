"""Canonical Icom actuator contract for runtime-managed transmit effects."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.command_map import CommandMap
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.bound import BoundCommands
from rigplane.commands.commander import IcomCommander, Priority
from rigplane.core.exceptions import ConnectionError
from rigplane.runtime.managed_tx_effect_lane import ManagedTxActuator
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.radio import IcomRadio
from test_serial_civ_link import _FakeWriter, _cleanup_writes, _make_link


class _TrackedTransport:
    def __init__(self, *, entered: asyncio.Event | None = None) -> None:
        self.my_id = 0x01020304
        self.remote_id = 0x05060708
        self.sent: list[bytes] = []
        self._entered = entered
        self.release = asyncio.Event()
        if entered is None:
            self.release.set()

    async def send_tracked(self, packet: bytes, **_kwargs: object) -> None:
        self.sent.append(packet)
        if self._entered is not None:
            self._entered.set()
        await self.release.wait()


def _profile_bound_radio() -> IcomRadio:
    radio = IcomRadio("", model="IC-7610")
    radio._commands = BoundCommands(
        CommandMap(
            {
                "ptt_on": (0x31, 0x01, 0xA1),
                "ptt_off": (0x31, 0x01, 0xA0),
                "stop_cw": (0x32, 0x02),
                "set_tuner_status": (0x33, 0x03),
            }
        )
    )
    return radio


def _token() -> EffectToken:
    return EffectToken(7, 3, "icom-actuator")


def test_icom_radio_satisfies_managed_tx_actuator_protocol() -> None:
    assert isinstance(_profile_bound_radio(), ManagedTxActuator)


@pytest.mark.asyncio
async def test_ptt_and_transmit_on_share_one_profile_command_and_immediate_lane() -> (
    None
):
    radio = _profile_bound_radio()
    radio._send_civ_raw = AsyncMock(return_value=None)

    def current() -> bool:
        return True

    results = [
        await radio.actuate(_token(), operation, is_current=current)
        for operation in (
            ActuationOperation.PTT_ON,
            ActuationOperation.TRANSMIT_ON,
        )
    ]

    assert results == [ActuationResult.ACCEPTED, ActuationResult.ACCEPTED]
    assert [call.args[0] for call in radio._send_civ_raw.await_args_list] == [
        radio._commands.ptt_on(to_addr=radio._radio_addr),
        radio._commands.ptt_on(to_addr=radio._radio_addr),
    ]
    for call in radio._send_civ_raw.await_args_list:
        assert call.kwargs == {
            "priority": Priority.IMMEDIATE,
            "wait_response": False,
            "is_current": current,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected", "priority"),
    [
        (
            ActuationOperation.FORCE_RECEIVE,
            (0x31, None, b"\x01\xa0"),
            Priority.FORCE_RELEASE,
        ),
        (AbortOperation.STOP_CW, (0x32, None, b"\x02\xff"), Priority.ABORT),
        (AbortOperation.STOP_TUNE, (0x33, None, b"\x03\x00"), Priority.ABORT),
    ],
)
async def test_release_and_abort_operations_use_profile_bytes_at_strict_priority(
    operation: ActuationOperation | AbortOperation,
    expected: tuple[int, int | None, bytes],
    priority: Priority,
) -> None:
    radio = _profile_bound_radio()
    radio._send_civ_raw = AsyncMock(return_value=None)

    def current() -> bool:
        return True

    result = await radio.actuate(_token(), operation, is_current=current)

    assert result is ActuationResult.ACCEPTED
    call = radio._send_civ_raw.await_args
    frame = parse_civ_frame(call.args[0])
    assert (frame.command, frame.sub, frame.data) == expected
    assert call.kwargs == {
        "priority": priority,
        "wait_response": False,
        "is_current": current,
    }


@pytest.mark.asyncio
async def test_force_release_overtakes_queued_abort_without_preempting_active() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    executed: list[bytes] = []

    async def execute(payload: bytes, _wait_response: bool) -> None:
        executed.append(payload)
        if payload == b"active":
            entered.set()
            await release.wait()

    commander = IcomCommander(execute, min_interval=0.0)
    commander.start()
    active = asyncio.create_task(commander.send(b"active", priority=Priority.NORMAL))
    abort = None
    late_on = None
    force = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        abort = asyncio.create_task(commander.send(b"stop-cw", priority=Priority.ABORT))
        late_on = asyncio.create_task(
            commander.send(b"late-on", priority=Priority.IMMEDIATE)
        )
        await asyncio.sleep(0)
        force = asyncio.create_task(
            commander.send(b"force-off", priority=Priority.FORCE_RELEASE)
        )
        await asyncio.sleep(0)

        assert executed == [b"active"]
        release.set()
        await asyncio.wait_for(asyncio.gather(active, force, abort, late_on), 1)
        assert executed == [b"active", b"force-off", b"stop-cw", b"late-on"]
    finally:
        release.set()
        for task in (active, force, abort, late_on):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (active, force, abort, late_on) if task is not None),
            return_exceptions=True,
        )
        await commander.stop()


@pytest.mark.asyncio
async def test_unsupported_profile_refuses_before_any_io() -> None:
    radio = IcomRadio("", model="IC-7610")
    radio._commands = BoundCommands(
        CommandMap({}),
        {"ptt_on": "test manufacturer command table"},
    )
    radio._send_civ_raw = AsyncMock(side_effect=AssertionError("I/O attempted"))

    result = await radio.actuate(
        _token(), ActuationOperation.PTT_ON, is_current=lambda: True
    )

    assert result is ActuationResult.REJECTED
    radio._send_civ_raw.assert_not_awaited()


@pytest.mark.asyncio
async def test_commander_propagates_currency_to_final_executor() -> None:
    observed: list[Callable[[], bool] | None] = []

    async def execute(
        _payload: bytes,
        _wait_response: bool,
        *,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        observed.append(is_current)

    commander = IcomCommander(execute, min_interval=0.0)

    def current() -> bool:
        return True

    commander.start()
    try:
        await commander.send(b"managed", is_current=current)
    finally:
        await commander.stop()

    assert observed == [current]


@pytest.mark.asyncio
async def test_provider_replacement_cannot_retarget_queued_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _profile_bound_radio()
    entered = asyncio.Event()
    old_transport = _TrackedTransport(entered=entered)
    replacement = _TrackedTransport()
    radio._civ_transport = old_transport
    radio._connected = True
    radio._civ_min_interval = 0.0
    monkeypatch.setattr(radio._civ_runtime, "start_pump", lambda: None)
    commander = IcomCommander(radio._civ_runtime.execute_civ_raw, min_interval=0.0)
    radio._commander = commander
    commander.start()
    blocker = asyncio.create_task(
        radio._send_civ_raw(
            build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x55, data=b"\x01"),
            wait_response=False,
        )
    )
    currency = {"current": True}
    on = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        on = asyncio.create_task(
            radio.actuate(
                _token(),
                ActuationOperation.PTT_ON,
                is_current=lambda: currency["current"],
            )
        )
        await asyncio.sleep(0)
        currency["current"] = False
        radio._civ_transport = replacement
        old_transport.release.set()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(blocker, 1)
        with pytest.raises(ConnectionError, match="managed TX attempt is stale"):
            await asyncio.wait_for(on, 1)
        assert len(old_transport.sent) == 1
        assert replacement.sent == []
    finally:
        old_transport.release.set()
        for task in (blocker, on):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (blocker, on) if task is not None),
            return_exceptions=True,
        )
        await commander.stop()
        radio._commander = None
        radio._civ_transport = None
        radio._connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_response", [False, True])
async def test_serial_writer_rechecks_managed_currency(
    monkeypatch: pytest.MonkeyPatch, wait_response: bool
) -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(writer=_FakeWriter(drain_gate=gate))
    transport = SerialCivTransport(link)
    radio = _profile_bound_radio()
    radio._civ_transport = transport
    radio._connected = True
    monkeypatch.setattr(radio._civ_runtime, "start_pump", lambda: None)
    state = {"current": True, "calls": 0}
    entered = asyncio.Event()
    on = None

    def is_current() -> bool:
        state["calls"] += 1
        if state["calls"] == 1 + wait_response:
            entered.set()
        return state["current"]

    try:
        await link.send(b"\x98\xe0\x03")
        await asyncio.wait_for(writer.drain_started.wait(), 1)
        on = asyncio.create_task(
            radio._civ_runtime.execute_civ_raw(
                radio._commands.ptt_on(to_addr=radio._radio_addr),
                wait_response=wait_response,
                is_current=is_current,
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        state["current"] = False
        gate.set()
        with pytest.raises(ConnectionError, match="managed TX attempt is stale"):
            await asyncio.wait_for(on, 1)
        assert len(writer.writes) == 1, "stale ON reached serial writer"
        assert transport.send_seq == transport._udp_error_count == 0
        assert radio._civ_request_tracker.pending_count == 0
        assert link.ready and link._write_queue.empty()
    finally:
        await _cleanup_writes(link, [on] if on else [], writer)
