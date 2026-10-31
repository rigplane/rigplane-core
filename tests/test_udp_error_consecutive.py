"""Consecutive UDP error counting (MOR-1832).

``IcomTransport._udp_error_count`` counts *consecutive* errors: any inbound
datagram resets it, so isolated ICMP blips hours apart must not latch
``IcomRadio.connected`` False. A separate lifetime counter,
``_udp_error_total``, keeps the MOR-1789 log cadence stable across
recoveries.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator

import pytest

from rigplane.core.transport import CONTROL_SIZE, IcomTransport, _UdpProtocol
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.types import HEADER_SIZE


def _good_packet(seq: int = 0) -> bytes:
    """Build a minimal valid 0x10-byte idle control packet."""
    pkt = bytearray(CONTROL_SIZE)
    struct.pack_into("<I", pkt, 0, CONTROL_SIZE)
    struct.pack_into("<H", pkt, 4, 0x00)
    struct.pack_into("<H", pkt, 6, seq)
    struct.pack_into("<I", pkt, 8, 0xAABBCCDD)
    struct.pack_into("<I", pkt, 0x0C, 0)
    return bytes(pkt)


def _blip(protocol: _UdpProtocol) -> None:
    """Record one transient UDP error (e.g. ICMP port-unreachable)."""
    protocol.error_received(OSError("Broken pipe"))


@pytest.fixture
def radio() -> Iterator[IcomRadio]:
    """Fresh radio that never touches the network; reset for __del__."""
    r = IcomRadio("192.168.1.100", timeout=0.05, model="IC-7610")
    yield r
    r._conn_state = RadioConnectionState.DISCONNECTED


def _attach_connected_civ(
    radio: IcomRadio,
) -> tuple[IcomTransport, _UdpProtocol]:
    """Attach a real CI-V transport and mark the session established."""
    civ = IcomTransport()
    radio._civ_transport = civ
    radio._conn_state = RadioConnectionState.CONNECTED
    return civ, _UdpProtocol(civ)


def test_isolated_blips_keep_connected(radio: IcomRadio) -> None:
    """Three errors each followed by good traffic keep connected True."""
    civ, protocol = _attach_connected_civ(radio)
    for _ in range(3):
        _blip(protocol)
        assert radio.connected is True
        civ._handle_packet(_good_packet())
    assert radio.connected is True
    assert civ._udp_error_count == 0


def test_consecutive_errors_latch_until_good_packet(
    radio: IcomRadio,
) -> None:
    """Three errors in a row report disconnected; one packet recovers."""
    civ, protocol = _attach_connected_civ(radio)
    for _ in range(3):
        _blip(protocol)
    assert radio.connected is False
    civ._handle_packet(_good_packet())
    assert civ._udp_error_count == 0
    assert radio.connected is True


def test_runt_datagram_resets_count(radio: IcomRadio) -> None:
    """Even a runt datagram proves the link is alive and resets the run."""
    civ, protocol = _attach_connected_civ(radio)
    for _ in range(3):
        _blip(protocol)
    assert radio.connected is False
    civ._handle_packet(b"\x00" * (HEADER_SIZE - 1))
    assert civ._udp_error_count == 0
    assert radio.connected is True


def test_control_transport_unreachable_tracks_current_run(
    radio: IcomRadio,
) -> None:
    """The remote-control banner follows the current error run, not history."""
    ctrl = IcomTransport()
    ctrl._udp_transport = object()  # type: ignore[assignment]
    protocol = _UdpProtocol(ctrl)
    radio._ctrl_transport = ctrl
    radio._conn_state = RadioConnectionState.RECONNECTING
    for _ in range(3):
        _blip(protocol)
    assert radio.remote_control_unreachable is True
    ctrl._handle_packet(_good_packet())
    for _ in range(3):
        _blip(protocol)
        assert radio.remote_control_unreachable is False
        ctrl._handle_packet(_good_packet())
    assert radio.remote_control_unreachable is False
    assert ctrl._udp_error_count == 0
    for _ in range(3):
        _blip(protocol)
    assert radio.remote_control_unreachable is True


def test_error_total_is_lifetime_and_drives_log_cadence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Total errors accumulate across recoveries and throttle the log."""
    transport = IcomTransport()
    protocol = _UdpProtocol(transport)
    with caplog.at_level(logging.WARNING, logger="rigplane.core.transport"):
        for _ in range(3):
            _blip(protocol)
            transport._handle_packet(_good_packet())
        _blip(protocol)
    assert transport._udp_error_count == 1
    assert transport._udp_error_total == 4
    records = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "UDP error" in rec.message
    ]
    assert len(records) == 3
    assert "(#1)" in records[0].message
    assert "(#2)" in records[1].message
    assert "(#3)" in records[2].message
