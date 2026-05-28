"""Response-capable raw CI-V transaction path (MOR-169)."""

from __future__ import annotations

import asyncio

import pytest
from test_radio import MockTransport

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.civ import (
    CivEvent,
    CivEventType,
    CivRequestTracker,
    request_key_from_frame,
)
from rigplane.core.exceptions import ConnectionError as RigplaneConnectionError
from rigplane.radio import IcomRadio
from rigplane.types import bcd_encode


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(transport: MockTransport):
    r = IcomRadio("192.168.1.100", timeout=0.05)
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    r._radio_addr = IC_7610_ADDR
    r._civ_ack_sink_grace = 0.001
    yield r
    r._connected = False
    r._civ_transport = None
    r._ctrl_transport = None


def _ack() -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFB)


def _nak() -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFA)


async def test_raw_civ_transaction_ack_success_releases_owner(
    radio: IcomRadio, transport: MockTransport
) -> None:
    transport.queue_response_on_send(1, _wrap(_ack()))

    result = await radio.send_civ_transaction(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        expect="ack",
        timeout=0.2,
    )

    assert result.status == "ack"
    assert result.frame_bytes == _ack()
    assert result.frame.command == 0xFB
    assert radio.external_cat_session_active is False
    assert transport.sent_packets[-1].endswith(b"\x1a\x05\x01\x53\x01\xfd")


async def test_raw_civ_transaction_data_response_success(
    radio: IcomRadio, transport: MockTransport
) -> None:
    response = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x03,
        data=bcd_encode(14_074_000),
    )
    transport.queue_response_on_send(1, _wrap(response))

    result = await radio.send_civ_transaction(0x03, expect="data", timeout=0.2)

    assert result.status == "response"
    assert result.frame_bytes == response
    assert result.frame.command == 0x03
    assert result.frame.data == bcd_encode(14_074_000)


async def test_raw_civ_transaction_command29_response_preserves_wire_bytes(
    radio: IcomRadio, transport: MockTransport
) -> None:
    response = bytes.fromhex("FEFEE098290103FD")
    transport.queue_response_on_send(1, _wrap(response))

    result = await radio.send_civ_transaction(
        0x29,
        data=b"\x01\x03",
        expect="data",
        timeout=0.2,
    )

    assert result.status == "response"
    assert result.frame.command == 0x03
    assert result.frame.receiver == 0x01
    assert result.frame_bytes == response


async def test_raw_civ_transaction_expect_none_is_fire_and_forget(
    radio: IcomRadio, transport: MockTransport
) -> None:
    result = await radio.send_civ_transaction(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        expect="none",
        timeout=0.2,
    )

    assert result.status == "sent"
    assert result.frame is None
    assert result.frame_bytes is None
    assert radio.external_cat_session_active is False
    assert transport.sent_packets[-1].endswith(b"\x1a\x05\x01\x53\x01\xfd")


async def test_raw_civ_transaction_nak_is_deterministic_failure_result(
    radio: IcomRadio, transport: MockTransport
) -> None:
    transport.queue_response_on_send(1, _wrap(_nak()))

    result = await radio.send_civ_transaction(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        expect="ack",
        timeout=0.2,
    )

    assert result.status == "nak"
    assert result.frame.command == 0xFA
    assert radio.external_cat_session_active is False


async def test_raw_civ_transaction_data_nak_is_deterministic_failure_result(
    radio: IcomRadio, transport: MockTransport
) -> None:
    transport.queue_response_on_send(1, _wrap(_nak()))

    result = await radio.send_civ_transaction(0x03, expect="data", timeout=0.2)

    assert result.status == "nak"
    assert result.frame.command == 0xFA
    assert radio.external_cat_session_active is False


async def test_raw_civ_transaction_ack_ignores_orphan_ack_backlog(
    radio: IcomRadio,
) -> None:
    tracker = radio._civ_request_tracker
    assert tracker.resolve(
        CivEvent(type=CivEventType.ACK, frame=parse_civ_frame(_ack()))
    )
    assert tracker.resolve(
        CivEvent(type=CivEventType.NAK, frame=parse_civ_frame(_nak()))
    )

    with pytest.raises(TimeoutError):
        await radio.send_civ_transaction(
            0x1A,
            sub=0x05,
            data=b"\x01\x53\x01",
            expect="ack",
            timeout=0.01,
        )

    assert radio.external_cat_session_active is False
    assert tracker.pending_count == 0


async def test_raw_civ_transaction_rejects_missing_civ_transport_without_claim(
    radio: IcomRadio,
) -> None:
    radio._civ_transport = None

    with pytest.raises(RigplaneConnectionError, match="Not connected to radio"):
        await radio.send_civ_transaction(
            0x1A,
            sub=0x05,
            data=b"\x01\x53\x01",
            expect="ack",
            timeout=0.01,
        )

    assert radio.external_cat_session_active is False
    assert radio._civ_request_tracker.pending_count == 0


async def test_raw_civ_transaction_cleans_stale_waiters_and_backlog(
    radio: IcomRadio, transport: MockTransport
) -> None:
    tracker = CivRequestTracker(stale_ttl=0.0)
    radio._civ_request_tracker = tracker
    radio._civ_epoch = tracker.generation
    radio._civ_waiter_ttl_gc_interval = 0.0

    assert tracker.resolve(
        CivEvent(type=CivEventType.ACK, frame=parse_civ_frame(_ack()))
    )

    stale_ack = tracker.register_ack(wait=True, consume_backlog=False)
    stale_response = tracker.register_response(
        request_key_from_frame(
            parse_civ_frame(build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x03))
        )
    )
    assert tracker.pending_count == 2

    transport.queue_response_on_send(1, _wrap(_ack()))

    result = await radio.send_civ_transaction(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        expect="ack",
        timeout=0.2,
    )

    assert result.status == "ack"
    assert tracker.pending_count == 0
    assert tracker.snapshot_stats()["stale_cleaned"] == 2
    assert tracker.snapshot_stats()["ack_backlog_drops"] == 1
    with pytest.raises(asyncio.TimeoutError, match="CI-V ACK waiter expired"):
        stale_ack.result()
    with pytest.raises(asyncio.TimeoutError, match="CI-V response waiter expired"):
        stale_response.result()


async def test_raw_civ_transaction_timeout_releases_owner(radio: IcomRadio) -> None:
    with pytest.raises(TimeoutError):
        await radio.send_civ_transaction(0x03, expect="data", timeout=0.01)

    assert radio.external_cat_session_active is False
    assert radio._civ_request_tracker.pending_count == 0
    assert radio._civ_request_tracker.timeout_count == 1


async def test_raw_civ_transaction_rejects_competing_owner(radio: IcomRadio) -> None:
    radio.begin_external_cat_session()

    try:
        with pytest.raises(RuntimeError, match="CI-V stream is already owned"):
            await radio.send_civ_transaction(0x03, expect="data", timeout=0.01)
    finally:
        radio.end_external_cat_session()


async def test_external_cat_begin_rejects_active_raw_transaction(
    radio: IcomRadio,
) -> None:
    task = asyncio.create_task(
        radio.send_civ_transaction(0x03, expect="data", timeout=1.0)
    )
    await asyncio.sleep(0)
    assert radio.external_cat_session_active is True

    with pytest.raises(RuntimeError, match="CI-V stream is already owned"):
        radio.begin_external_cat_session()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert radio.external_cat_session_active is False
    assert radio._civ_request_tracker.pending_count == 0
    assert radio._civ_request_tracker.timeout_count == 0


async def test_raw_civ_transaction_session_releases_on_cancellation(
    radio: IcomRadio,
) -> None:
    task = asyncio.create_task(
        radio.send_civ_transaction(0x03, expect="data", timeout=1.0)
    )
    await asyncio.sleep(0)
    assert radio.external_cat_session_active is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert radio.external_cat_session_active is False
    assert radio._civ_request_tracker.pending_count == 0
    assert radio._civ_request_tracker.timeout_count == 0


def _wrap(civ_frame: bytes) -> bytes:
    pkt = bytearray(0x16 + len(civ_frame))
    pkt[0x10] = 0xC1
    pkt[0x16:] = civ_frame
    return bytes(pkt)
