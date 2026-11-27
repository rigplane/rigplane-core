"""MOR-4856: abandoned writes retain ownership of their late bare reply."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable

import pytest
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_radio import MockTransport

from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core import civ
from rigplane.core.civ import CivEvent, CivEventType, CivRequestKey, CivRequestTracker
from rigplane.core.exceptions import CommandError, TimeoutError as CivTimeoutError
from rigplane.runtime import _civ_rx
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import VirtualClock
from rigplane_testkit.leaked_radio_guard import fake_connected

pytestmark = pytest.mark.timeout(10)


def _reply(command: int) -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, 0x98, command)


@pytest.fixture(params=["lan", "serial"])
async def wired_radio(
    request: pytest.FixtureRequest,
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[IcomRadio, Callable[[bytes], None], list[bytes]], None]:
    virtual_clock.scope_to(monkeypatch, civ)
    virtual_clock.scope_to(monkeypatch, _civ_rx)
    radio = IcomRadio("127.0.0.1", timeout=2.0, model="IC-7610")
    if request.param == "serial":
        link = _FakeSerialCivLink()
        link.connected = True

        async def send_without_reply(frame: bytes) -> None:
            link.sent_frames.append(bytes(frame))

        monkeypatch.setattr(link, "send", send_without_reply)
        transport = SerialCivTransport(link)
        inject = link.queue_response
        sent = link.sent_frames
    else:
        transport = MockTransport()

        def inject(frame: bytes) -> None:
            transport.queue_response(radio._wrap_civ(frame))

        sent = transport.sent_packets
    radio._civ_transport = transport
    fake_connected(radio)
    radio._civ_min_interval = 0.0
    try:
        yield radio, inject, sent
    finally:
        await radio._civ_runtime.stop_pump()
        radio._civ_request_tracker.fail_all(RuntimeError("test cleanup"))


@pytest.mark.parametrize("late_command", [0xFB, 0xFA])
@pytest.mark.parametrize("arrives_before_next_write", [False, True])
@pytest.mark.parametrize("retire_via", ["unregister", "stale-gc"])
async def test_abandoned_write_consumes_late_reply_without_backlogging(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    late_command: int,
    arrives_before_next_write: bool,
    retire_via: str,
) -> None:
    virtual_clock.scope_to(monkeypatch, civ)
    tracker = CivRequestTracker()
    first = tracker.register_ack(consume_backlog=False)
    assert isinstance(first, asyncio.Future)
    tracker.mark_sent(virtual_clock(), futures=(first,))
    await virtual_clock.elapse(IcomRadio.ICOM_PTT_ON_ACK_TIMEOUT_S + 0.01)
    if retire_via == "unregister":
        tracker.unregister(first)
        first.cancel()

    # Neither ordinary sink expiry nor stale GC may erase the reply debt.
    await virtual_clock.elapse(11.0)
    tracker.expire_ack_sinks(answer_window=2.0)
    tracker.cleanup_stale()
    if retire_via == "stale-gc":
        with pytest.raises(asyncio.TimeoutError):
            first.result()
    assert tracker.pending_count == 0
    assert tracker.ack_sink_count == 0
    late = CivEvent(
        type=CivEventType.ACK if late_command == 0xFB else CivEventType.NAK,
        frame=parse_civ_frame(_reply(late_command)),
    )
    if arrives_before_next_write:
        assert tracker.resolve(late)

    second = tracker.register_ack(consume_backlog=True)
    assert isinstance(second, asyncio.Future)
    tracker.mark_sent(virtual_clock(), futures=(second,))
    if not arrives_before_next_write:
        assert tracker.resolve(late)
    assert not second.done(), "write A's late reply settled write B"
    own = parse_civ_frame(_reply(0xFA if late_command == 0xFB else 0xFB))
    assert tracker.resolve(
        CivEvent(
            type=CivEventType.NAK if own.command == 0xFA else CivEventType.ACK,
            frame=own,
        )
    )
    assert second.result() is own
    assert tracker.pending_count == 0


@pytest.mark.parametrize("first_path", ["ptt", "write", "raw", "cancelled"])
@pytest.mark.parametrize("late_command,own_command", [(0xFB, 0xFA), (0xFA, 0xFB)])
async def test_expired_write_cannot_decide_next_write_through_receive_pump(
    virtual_clock: VirtualClock,
    wired_radio: tuple[IcomRadio, Callable[[bytes], None], list[bytes]],
    first_path: str,
    late_command: int,
    own_command: int,
) -> None:
    radio, inject, sent = wired_radio
    write = build_civ_frame(0x98, CONTROLLER_ADDR, 0x14, sub=0x01, data=b"\x01\x10")
    tasks = []
    try:
        if first_path == "ptt":
            first = asyncio.create_task(
                radio.actuate(
                    EffectToken(0, 0, "late-ack"),
                    ActuationOperation.PTT_ON,
                    is_current=lambda: True,
                )
            )
            timeout = radio.ICOM_PTT_ON_ACK_TIMEOUT_S
        elif first_path == "raw":
            timeout = radio.ICOM_PTT_ON_ACK_TIMEOUT_S
            first = asyncio.create_task(
                radio._civ_runtime.execute_civ_transaction(
                    write, expect="ack", timeout=timeout
                )
            )
        else:
            timeout = radio._civ_get_timeout
            first = asyncio.create_task(radio._execute_civ_raw(write))
        tasks.append(first)
        await virtual_clock.elapse(0)
        assert len(sent) == 1
        if first_path == "cancelled":
            first.cancel()
            await virtual_clock.elapse(0)
        else:
            await virtual_clock.elapse(timeout + 0.01)
        assert first.done()
        if first_path == "ptt":
            assert await first is ActuationResult.ACCEPTED
        elif first_path == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            with pytest.raises((CivTimeoutError, asyncio.TimeoutError)):
                await first

        second = asyncio.create_task(radio._execute_civ_raw(write))
        tasks.append(second)
        await virtual_clock.elapse(0)
        assert len(sent) == 2
        inject(_reply(late_command))
        await virtual_clock.elapse(0)
        assert not second.done(), "write A's late reply settled write B"
        inject(_reply(own_command))
        await virtual_clock.elapse(0)
        assert second.done()
        assert (await second).command == own_command
        assert radio._civ_request_tracker.pending_count == 0
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("first_path", ["write", "raw"])
async def test_proven_pre_wire_refusal_does_not_consume_next_write_reply(
    virtual_clock: VirtualClock,
    wired_radio: tuple[IcomRadio, Callable[[bytes], None], list[bytes]],
    monkeypatch: pytest.MonkeyPatch,
    first_path: str,
) -> None:
    radio, inject, sent = wired_radio
    transport = radio._civ_transport
    original_send = transport.send_tracked
    write = build_civ_frame(0x98, CONTROLLER_ADDR, 0x14, sub=0x01, data=b"\x01\x10")

    async def refuse(*args, **kwargs) -> None:
        raise CommandError("refused before writing")

    monkeypatch.setattr(transport, "send_tracked", refuse)
    if first_path == "raw":
        with pytest.raises(CommandError):
            await radio._civ_runtime.execute_civ_transaction(write, expect="ack")
    else:
        with pytest.raises(CommandError):
            await radio._execute_civ_raw(write)
    assert sent == []
    monkeypatch.setattr(transport, "send_tracked", original_send)
    second = asyncio.create_task(radio._execute_civ_raw(write))
    try:
        await virtual_clock.elapse(0)
        inject(_reply(0xFB))
        await virtual_clock.elapse(0)
        assert second.done(), "a write refused before the wire retained a reply sink"
        assert (await second).command == 0xFB
    finally:
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)


@pytest.mark.parametrize("command", [0xFB, 0xFA])
async def test_timely_write_reply_still_settles_its_waiter(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, command: int
) -> None:
    virtual_clock.scope_to(monkeypatch, civ)
    tracker = CivRequestTracker()
    for _ in range(2):
        pending = tracker.register_ack(consume_backlog=False)
        assert isinstance(pending, asyncio.Future)
        tracker.mark_sent(virtual_clock(), futures=(pending,))
        await virtual_clock.elapse(0.01)
        reply = parse_civ_frame(_reply(command))
        assert tracker.resolve(
            CivEvent(
                type=CivEventType.ACK if command == 0xFB else CivEventType.NAK,
                frame=reply,
            )
        )
        assert pending.result() is reply
        tracker.unregister(pending)
    assert tracker.pending_count == 0


@pytest.mark.parametrize("retire_via", ["ordered-data", "generation"])
async def test_retired_write_debt_leaves_on_ordered_evidence_or_session_end(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    retire_via: str,
) -> None:
    virtual_clock.scope_to(monkeypatch, civ)
    tracker = CivRequestTracker()
    first = tracker.register_ack(consume_backlog=False)
    assert isinstance(first, asyncio.Future)
    tracker.mark_sent(virtual_clock(), futures=(first,))
    tracker.unregister(first)
    first.cancel()
    if retire_via == "generation":
        tracker.advance_generation()
    else:
        read = tracker.register_response(CivRequestKey(command=0x03, sub=None))
        tracker.mark_sent(virtual_clock(), futures=(read,))
        response = parse_civ_frame(
            build_civ_frame(CONTROLLER_ADDR, 0x98, 0x03, data=b"\x00\x40\x07\x14\x00")
        )
        assert tracker.resolve(
            CivEvent(type=CivEventType.RESPONSE, frame=response), ordered_ingress=True
        )
        assert read.result() is response
    second = tracker.register_ack(consume_backlog=False)
    assert isinstance(second, asyncio.Future)
    tracker.mark_sent(virtual_clock(), futures=(second,))
    own = parse_civ_frame(_reply(0xFB))
    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=own))
    assert second.result() is own


@pytest.mark.parametrize("command", [0xFB, 0xFA])
async def test_timely_ptt_reply_preserves_acceptance_and_refusal(
    virtual_clock: VirtualClock,
    wired_radio: tuple[IcomRadio, Callable[[bytes], None], list[bytes]],
    command: int,
) -> None:
    radio, inject, _ = wired_radio
    task = asyncio.create_task(
        radio.actuate(
            EffectToken(0, 0, "timely-ack"),
            ActuationOperation.PTT_ON,
            is_current=lambda: True,
        )
    )
    try:
        await virtual_clock.elapse(0)
        inject(_reply(command))
        await virtual_clock.elapse(0)
        assert task.done()
        expected = (
            ActuationResult.ACCEPTED if command == 0xFB else ActuationResult.REJECTED
        )
        assert await task is expected
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
