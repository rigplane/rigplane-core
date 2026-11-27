"""MOR-4885: outage-time key requests must not reach a recovered link."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from test_radio import MockTransport

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.exceptions import StaleBeforeWireError
from rigplane.runtime import _civ_rx
from rigplane.runtime._civ_rx import CIV_HEADER_SIZE
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import VirtualClock


@dataclass
class _Outage:
    radio: IcomRadio
    restored_link: MockTransport
    restore: Callable[[], None]


@pytest.fixture(params=["IC-7300", "IC-7610"])
async def outage(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> AsyncIterator[_Outage]:
    radio = IcomRadio("test.invalid", model=request.param)
    radio._ctrl_transport = SimpleNamespace(_udp_transport=object())
    radio._conn_state = RadioConnectionState.RECONNECTING
    radio._civ_recovering = True
    radio._civ_transport = None
    radio._civ_min_interval = 0.0
    radio._civ_recovery_wait_timeout = 12.0
    restored_link = MockTransport()
    restored = asyncio.Event()
    monkeypatch.setattr(_civ_rx, "time", SimpleNamespace(monotonic=virtual_clock))

    async def reconnect() -> None:
        await restored.wait()

    monkeypatch.setattr(radio, "soft_reconnect", reconnect)

    def restore() -> None:
        radio._civ_transport = restored_link
        radio._conn_state = RadioConnectionState.CONNECTED
        radio._civ_recovering = False
        restored.set()

    try:
        yield _Outage(radio, restored_link, restore)
    finally:
        restored.set()
        fast_reconnect = radio._civ_runtime._fast_reconnect_task
        if fast_reconnect is not None:
            await fast_reconnect
        await radio._civ_runtime.stop_worker()
        await radio._civ_runtime.stop_pump()
        radio._conn_state = RadioConnectionState.DISCONNECTED
        radio._civ_transport = None


def _frame(radio: IcomRadio, operation: str, wrapped: bool) -> bytes:
    commands = radio._commands
    address = radio._radio_addr
    if operation == "ptt_on":
        frame = commands.ptt_on(to_addr=address)
    elif operation == "ptt_off":
        frame = commands.ptt_off(to_addr=address)
    elif operation == "cw":
        frame = commands.send_cw("TEST", to_addr=address)[0]
    elif operation == "stop_cw":
        frame = commands.stop_cw(to_addr=address)
    elif operation == "tune":
        frame = commands.set_tuner_status(2, to_addr=address)
    elif operation == "stop_tune":
        frame = commands.set_tuner_status(0, to_addr=address)
    elif operation == "voice":
        frame = build_civ_frame(address, CONTROLLER_ADDR, 0x28, 0x00, b"\x01")
    elif operation == "stop_voice":
        frame = build_civ_frame(address, CONTROLLER_ADDR, 0x28, 0x00, b"\x00")
    elif operation == "ptt_read":
        frame = build_civ_frame(address, CONTROLLER_ADDR, 0x1C, 0x00)
    elif operation == "tuner_read":
        frame = commands.get_tuner_status(to_addr=address)
    elif operation == "frequency_read":
        frame = commands.get_freq(to_addr=address)
    else:
        raise AssertionError(operation)
    if wrapped:
        return frame[:4] + b"\x29\x00" + frame[4:]
    return frame


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize("operation", ["ptt_on", "cw", "tune", "voice"])
@pytest.mark.parametrize("wrapped", [False, True], ids=["bare", "receiver-prefix"])
@pytest.mark.parametrize("stalled", [False, True], ids=["missing", "stalled"])
async def test_outage_key_fails_before_recovery_and_never_reaches_restored_link(
    outage: _Outage,
    virtual_clock: VirtualClock,
    operation: str,
    wrapped: bool,
    stalled: bool,
) -> None:
    radio = outage.radio
    old_link = MockTransport()
    if stalled:
        radio._civ_transport = old_link
    frame = _frame(radio, operation, wrapped)
    caller = asyncio.create_task(radio._send_civ_raw(frame, wait_response=False))
    try:
        await virtual_clock.elapse(0.0)
        failed_without_wait = caller.done()
        await virtual_clock.elapse(0.5)
        outage.restore()
        await virtual_clock.elapse(0.25)
        assert caller.done()
        result = (await asyncio.gather(caller, return_exceptions=True))[0]
        assert outage.restored_link.sent_packets == []
        assert old_link.sent_packets == []
        assert isinstance(result, StaleBeforeWireError)
        assert failed_without_wait
    finally:
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "operation",
    [
        "ptt_off",
        "stop_cw",
        "stop_tune",
        "stop_voice",
        "ptt_read",
        "tuner_read",
        "frequency_read",
    ],
)
@pytest.mark.parametrize("wrapped", [False, True], ids=["bare", "receiver-prefix"])
async def test_outage_non_key_command_waits_and_is_delivered_after_recovery(
    outage: _Outage,
    virtual_clock: VirtualClock,
    operation: str,
    wrapped: bool,
) -> None:
    frame = _frame(outage.radio, operation, wrapped)
    caller = asyncio.create_task(outage.radio._send_civ_raw(frame, wait_response=False))
    try:
        await virtual_clock.elapse(0.5)
        assert not caller.done()
        assert outage.restored_link.sent_packets == []
        outage.restore()
        await virtual_clock.elapse(0.25)
        assert caller.done()
        assert await caller is None
        assert [
            packet[CIV_HEADER_SIZE:] for packet in outage.restored_link.sent_packets
        ] == [frame]
    finally:
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize("operation", ["ptt_on", "cw", "tune", "voice"])
async def test_connected_link_still_delivers_key_commands(
    outage: _Outage, operation: str
) -> None:
    outage.restore()
    frame = _frame(outage.radio, operation, wrapped=False)
    assert await outage.radio._send_civ_raw(frame, wait_response=False) is None
    assert [
        packet[CIV_HEADER_SIZE:] for packet in outage.restored_link.sent_packets
    ] == [frame]
