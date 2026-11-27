"""MOR-4885: outage-time key requests must not reach a recovered link."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from test_radio import MockTransport
from test_yaesu_audio import FakeAudioDriver

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.exceptions import StaleBeforeWireError
from rigplane.exceptions import ConnectionError as RadioConnectionError
from rigplane.exceptions import TimeoutError as RadioTimeoutError
from rigplane.runtime import _civ_rx
from rigplane.runtime._civ_rx import CIV_HEADER_SIZE
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import VirtualClock, await_until
from rigplane_testkit.yaesu import FakeCatRadio


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
    monkeypatch.delenv("ICOM_CIV_RECOVERY_WAIT_TIMEOUT_S", raising=False)
    radio = IcomRadio("test.invalid", model=request.param)
    assert radio._civ_recovery_wait_timeout == 12.0
    radio._ctrl_transport = SimpleNamespace(_udp_transport=object())
    radio._conn_state = RadioConnectionState.RECONNECTING
    radio._civ_recovering = True
    radio._civ_transport = None
    radio._civ_min_interval = 0.0
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
@pytest.mark.parametrize("recovery_delay", [0.5, 11.7])
async def test_outage_key_fails_before_recovery_and_never_reaches_restored_link(
    outage: _Outage,
    virtual_clock: VirtualClock,
    operation: str,
    wrapped: bool,
    stalled: bool,
    recovery_delay: float,
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
        await virtual_clock.elapse(recovery_delay)
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
@pytest.mark.parametrize("recovery_delay", [0.5, 11.7])
async def test_outage_non_key_command_waits_and_is_delivered_after_recovery(
    outage: _Outage,
    virtual_clock: VirtualClock,
    operation: str,
    wrapped: bool,
    recovery_delay: float,
) -> None:
    frame = _frame(outage.radio, operation, wrapped)
    caller = asyncio.create_task(outage.radio._send_civ_raw(frame, wait_response=False))
    try:
        await virtual_clock.elapse(recovery_delay)
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
@pytest.mark.parametrize(
    ("operation", "release"),
    [
        ("ptt_on", "ptt_off"),
        ("cw", "stop_cw"),
        ("tune", "stop_tune"),
        ("voice", "stop_voice"),
    ],
)
@pytest.mark.parametrize("prefix", ["read", "release"])
@pytest.mark.parametrize("wrapped", [False, True], ids=["bare", "receiver-prefix"])
@pytest.mark.parametrize("stalled", [False, True], ids=["missing", "stalled"])
async def test_outage_combined_raw_key_payload_is_refused_before_recovery(
    outage: _Outage,
    virtual_clock: VirtualClock,
    operation: str,
    release: str,
    prefix: str,
    wrapped: bool,
    stalled: bool,
) -> None:
    radio = outage.radio
    old_link = MockTransport()
    if stalled:
        radio._civ_transport = old_link
    first = "frequency_read" if prefix == "read" else release
    payload = _frame(radio, first, wrapped) + _frame(radio, operation, wrapped)
    caller = asyncio.create_task(radio.send_civ_raw_fire_and_forget(payload))
    try:
        await virtual_clock.elapse(0.0)
        failed_without_wait = caller.done()
        await virtual_clock.elapse(11.7)
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
@pytest.mark.parametrize("operation", ["ptt_off", "stop_cw", "stop_tune", "stop_voice"])
@pytest.mark.parametrize("wrapped", [False, True], ids=["bare", "receiver-prefix"])
async def test_outage_release_times_out_at_default_deadline_without_recovery(
    outage: _Outage,
    virtual_clock: VirtualClock,
    operation: str,
    wrapped: bool,
) -> None:
    frame = _frame(outage.radio, operation, wrapped)
    caller = asyncio.create_task(outage.radio.send_civ_raw_fire_and_forget(frame))
    try:
        await virtual_clock.elapse(0.0)
        await virtual_clock.elapse(11.75)
        assert not caller.done()
        assert outage.restored_link.sent_packets == []
        await virtual_clock.elapse(0.25)
        assert caller.done()
        with pytest.raises(StaleBeforeWireError) as refused:
            await caller
        assert isinstance(refused.value.__cause__, RadioTimeoutError)
        assert str(refused.value.__cause__) == "CI-V transport recovery timed out"
        assert outage.restored_link.sent_packets == []
        outage.restore()
        await virtual_clock.elapse(0.25)
        assert outage.restored_link.sent_packets == []
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


@pytest.mark.asyncio
async def test_yaesu_ptt_on_while_disconnected_fails_fast_and_keys_nothing_after_reconnect() -> (
    None
):
    """MOR-4885 R3: the Yaesu CAT send path has no recovery wait.

    ``YaesuCatRadio._write`` refuses via ``_require_connected`` before
    touching the transport (``backends/yaesu_cat/radio.py``), so a key-type
    command issued while the link is down fails with the plain connection
    error instead of waiting out a recovery window — and the refused key is
    never replayed: after the owner reconnect nothing reaches the wire.

    Real ``YaesuCatRadio`` over the TCP ``FakeCatRadio``; the fake's
    wire-exact ``commands`` log and ``ptt`` state observe the wire. Real
    time with ``await_until`` like the other ``FakeCatRadio`` tests — no
    virtual clock, which no TCP-fake test combines with live socket I/O.
    """
    fake = FakeCatRadio()
    await fake.start()
    try:
        radio = YaesuCatRadio(
            device=fake.device_url,
            profile="ftx1",
            audio_driver=FakeAudioDriver(),  # type: ignore[arg-type]
        )
        try:
            await radio.connect()
            assert await await_until(lambda: radio.connected, timeout=5.0)
            await radio.disconnect()
            assert await await_until(lambda: not radio.connected, timeout=5.0)

            # Refused immediately: a 5 s bound fails closed on any
            # recovery-style wait instead of hanging on it.
            with pytest.raises(RadioConnectionError):
                await asyncio.wait_for(radio.set_ptt(True), timeout=5.0)
            assert fake.ptt is False
            assert "TX1;" not in fake.commands

            await radio.connect()
            assert await await_until(lambda: radio.connected, timeout=5.0)
            assert fake.ptt is False
            assert "TX1;" not in fake.commands

            # Positive control: the reconnected link keys, so the
            # assertions above observe a live wire, not a dead one.
            await radio.set_ptt(True)
            assert fake.ptt is True
            assert "TX1;" in fake.commands
            await radio.set_ptt(False)
            assert fake.ptt is False
        finally:
            await radio.disconnect()
    finally:
        await fake.stop()
