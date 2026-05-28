"""Raw CI-V pipe API (MOR-164) — transparent CI-V byte transport for Hamlib A1.

Covers the seam that replaces the MOR-159 monkeypatch:
- fire-and-forget raw TX that does not wait for / match a response;
- raw inbound listener that receives exact on-wire bytes incl. bare ACK frames;
- transceive-broadcast (to_addr == 0x00) filtering;
- subscription removal.
"""

from __future__ import annotations

import asyncio

import pytest
from test_radio import MockTransport

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.radio import IcomRadio
from rigplane.runtime.radio import RawCivSubscription
from rigplane.types import bcd_encode


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(transport: MockTransport):
    r = IcomRadio("192.168.1.100")
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    r._radio_addr = IC_7610_ADDR
    yield r
    # Teardown: drop the fake connection so the radio finalizer stays quiet.
    r._connected = False
    r._civ_transport = None
    r._ctrl_transport = None


def _ack() -> bytes:
    # Bare ACK from the radio: FE FE E0 98 FB FD
    return build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFB)


def test_listener_receives_bare_ack(radio: IcomRadio) -> None:
    received: list[bytes] = []
    sub = radio.add_raw_civ_listener(received.append)
    assert isinstance(sub, RawCivSubscription)

    ack = _ack()
    assert ack == bytes([0xFE, 0xFE, 0xE0, 0x98, 0xFB, 0xFD])
    radio._civ_runtime.deliver_raw_civ(ack)

    assert received == [ack]


def test_listener_receives_data_frame(radio: IcomRadio) -> None:
    received: list[bytes] = []
    radio.add_raw_civ_listener(received.append)

    frame = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x03, data=bcd_encode(14_074_000)
    )
    radio._civ_runtime.deliver_raw_civ(frame)

    assert received == [frame]


def test_transceive_broadcast_is_filtered(radio: IcomRadio) -> None:
    received: list[bytes] = []
    radio.add_raw_civ_listener(received.append)

    # Unsolicited transceive broadcast: to_addr == 0x00 — must NOT reach Hamlib.
    bcast = (
        bytes([0xFE, 0xFE, 0x00, IC_7610_ADDR, 0x00]) + bcd_encode(14_074_000) + b"\xfd"
    )
    radio._civ_runtime.deliver_raw_civ(bcast)

    assert received == []


def test_foreign_source_is_filtered(radio: IcomRadio) -> None:
    received: list[bytes] = []
    radio.add_raw_civ_listener(received.append)

    # Frame from a different radio address — not our session.
    frame = (
        bytes([0xFE, 0xFE, CONTROLLER_ADDR, 0xAA, 0x03])
        + bcd_encode(14_074_000)
        + b"\xfd"
    )
    radio._civ_runtime.deliver_raw_civ(frame)

    assert received == []


def test_subscription_close_stops_delivery(radio: IcomRadio) -> None:
    received: list[bytes] = []
    sub = radio.add_raw_civ_listener(received.append)
    ack = _ack()

    radio._civ_runtime.deliver_raw_civ(ack)
    assert len(received) == 1

    sub.close()
    radio._civ_runtime.deliver_raw_civ(ack)
    assert len(received) == 1  # no second delivery after close

    sub.close()  # idempotent — must not raise


def test_listener_failure_is_isolated(radio: IcomRadio) -> None:
    seen: list[bytes] = []

    def boom(_frame: bytes) -> None:
        raise RuntimeError("listener bug")

    radio.add_raw_civ_listener(boom)
    radio.add_raw_civ_listener(seen.append)

    ack = _ack()
    radio._civ_runtime.deliver_raw_civ(ack)  # must not propagate the exception
    assert seen == [ack]


async def test_fire_and_forget_transmits_without_waiting(
    radio: IcomRadio, transport: MockTransport
) -> None:
    # A write frame (set-freq) — the radio answers only with a bare ACK.
    frame = build_civ_frame(
        IC_7610_ADDR, CONTROLLER_ADDR, 0x05, data=bcd_encode(14_100_000)
    )

    # No response is ever queued; this must still return promptly (no matcher,
    # no timeout) rather than blocking on a response that never comes.
    await asyncio.wait_for(radio.send_civ_raw_fire_and_forget(frame), timeout=1.0)

    assert transport.sent_packets, "fire-and-forget must transmit the frame"
    assert frame in transport.sent_packets[-1]

    await radio._civ_runtime.stop_pump()
