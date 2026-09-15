"""Tests for token renewal mechanism."""

import asyncio
import struct

import pytest

from rigplane.radio import IcomRadio

from test_radio import MockTransport


@pytest.fixture
def radio() -> IcomRadio:
    r = IcomRadio("192.168.1.100", model="IC-7610")
    mt = MockTransport()
    r._ctrl_transport = mt
    r._connected = True
    r._token = 0xDEADBEEF
    r._tok_request = 0xABCD
    r._auth_seq = 10
    return r


class TestSendToken:
    @pytest.mark.asyncio
    async def test_sends_token_renewal(self, radio: IcomRadio) -> None:
        mt = radio._ctrl_transport
        await radio._send_token(0x05)
        assert len(mt.sent_packets) == 1
        pkt = mt.sent_packets[0]
        assert len(pkt) == 0x40
        # Check magic (requesttype)
        assert pkt[0x15] == 0x05
        # Check requestreply
        assert pkt[0x14] == 0x01
        # Check token
        token = struct.unpack_from("<I", pkt, 0x1C)[0]
        assert token == 0xDEADBEEF
        # Check tok_request
        tok_req = struct.unpack_from("<H", pkt, 0x1A)[0]
        assert tok_req == 0xABCD

    @pytest.mark.asyncio
    async def test_sends_token_remove(self, radio: IcomRadio) -> None:
        mt = radio._ctrl_transport
        await radio._send_token(0x01)
        pkt = mt.sent_packets[0]
        assert pkt[0x15] == 0x01

    @pytest.mark.asyncio
    async def test_sends_token_ack(self, radio: IcomRadio) -> None:
        mt = radio._ctrl_transport
        await radio._send_token(0x02)
        pkt = mt.sent_packets[0]
        assert pkt[0x15] == 0x02

    @pytest.mark.asyncio
    async def test_increments_auth_seq(self, radio: IcomRadio) -> None:
        assert radio._auth_seq == 10
        await radio._send_token(0x05)
        assert radio._auth_seq == 11
        await radio._send_token(0x05)
        assert radio._auth_seq == 12

    @pytest.mark.asyncio
    async def test_resetcap_field(self, radio: IcomRadio) -> None:
        mt = radio._ctrl_transport
        await radio._send_token(0x05)
        pkt = mt.sent_packets[0]
        resetcap = struct.unpack_from(">H", pkt, 0x24)[0]
        assert resetcap == 0x0798


class TestTokenRenewalLoop:
    @pytest.mark.asyncio
    async def test_start_stop(self, radio: IcomRadio) -> None:
        radio._control_phase._start_token_renewal()
        assert radio._token_task is not None
        assert not radio._token_task.done()
        radio._control_phase._stop_token_renewal()
        await asyncio.sleep(0.05)
        assert radio._token_task is None

    @pytest.mark.asyncio
    async def test_loop_sends_after_interval(self, radio: IcomRadio) -> None:
        # Shorten interval for testing
        radio._control_phase.TOKEN_RENEWAL_INTERVAL = 0.1
        mt = radio._ctrl_transport
        radio._control_phase._start_token_renewal()
        await asyncio.sleep(0.25)
        radio._control_phase._stop_token_renewal()
        # Should have sent at least 1 renewal
        assert len(mt.sent_packets) >= 1
        # Verify it's a token packet
        assert len(mt.sent_packets[0]) == 0x40
        assert mt.sent_packets[0][0x15] == 0x05

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self, radio: IcomRadio) -> None:
        radio._control_phase._stop_token_renewal()  # should not raise
