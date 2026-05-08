"""Tests for IcomRadio connect/disconnect lifecycle and internal helpers."""

import struct
import time
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.exceptions import (
    ConnectionError,
    TimeoutError,
)
from rigplane.radio import (
    CONNINFO_SIZE,
    IcomRadio,
    STATUS_SIZE,
    TOKEN_ACK_SIZE,
)
from rigplane.audio.route import AudioConfigSource
from rigplane.transport import ConnectionState
from rigplane.types import AudioCodec

from test_radio import MockTransport


# ---------------------------------------------------------------------------
# Extended MockTransport for connection tests
# ---------------------------------------------------------------------------


class ConnectMockTransport(MockTransport):
    """Extended mock that simulates the connection handshake."""

    def __init__(self) -> None:
        super().__init__()
        self.state = ConnectionState.DISCONNECTED

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: "object | None" = None,
    ) -> None:
        self.connected = True
        self.state = ConnectionState.CONNECTING
        self.connect_args = {
            "host": host,
            "port": port,
            "local_host": local_host,
            "local_port": local_port,
        }

    async def reconnect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
    ) -> None:
        self.connected = True
        self.state = ConnectionState.CONNECTING
        self.reconnect_args = {
            "host": host,
            "port": port,
            "local_host": local_host,
        }

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass

    def start_idle_loop(self) -> None:
        pass


def _build_login_response(
    success: bool = True,
    token: int = 0x12345678,
    tok_request: int = 0xABCD,
) -> bytes:
    """Build a fake 0x60-byte login response."""
    pkt = bytearray(0x60)
    struct.pack_into("<I", pkt, 0, 0x60)
    struct.pack_into("<H", pkt, 4, 0x00)
    if success:
        struct.pack_into("<I", pkt, 0x1C, token)
        struct.pack_into("<H", pkt, 0x1A, tok_request)
        struct.pack_into("<I", pkt, 0x20, 0x00000000)  # error = 0 (success)
    else:
        struct.pack_into("<I", pkt, 0x20, 0xFEFFFFFF)  # error = auth fail
    return bytes(pkt)


def _build_conninfo() -> bytes:
    """Build a fake 0x90-byte conninfo from radio."""
    pkt = bytearray(CONNINFO_SIZE)
    struct.pack_into("<I", pkt, 0, CONNINFO_SIZE)
    # GUID area
    pkt[0x20:0x30] = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"
    return bytes(pkt)


def _build_status(
    civ_port: int = 50002,
    audio_port: int = 50003,
    *,
    error: int = 0,
    disc: int = 0,
) -> bytes:
    """Build a fake 0x50-byte status packet."""
    pkt = bytearray(STATUS_SIZE)
    struct.pack_into("<I", pkt, 0, STATUS_SIZE)
    struct.pack_into("<I", pkt, 0x30, error)
    pkt[0x40] = disc
    struct.pack_into(">H", pkt, 0x42, civ_port)
    struct.pack_into(">H", pkt, 0x46, audio_port)
    return bytes(pkt)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSendTokenAck:
    @pytest.mark.asyncio
    async def test_sends_token_ack(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._token = 0x12345678
        radio._tok_request = 0xABCD
        await radio._control_phase._send_token_ack()
        assert len(mt.sent_packets) == 1
        pkt = mt.sent_packets[0]
        assert len(pkt) == TOKEN_ACK_SIZE
        # token should be at offset 0x1C
        token = struct.unpack_from("<I", pkt, 0x1C)[0]
        assert token == 0x12345678


class TestReceiveGuid:
    @pytest.mark.asyncio
    async def test_receives_guid(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_conninfo())
        guid = await radio._control_phase._receive_guid()
        assert guid is not None
        assert len(guid) == 16

    @pytest.mark.asyncio
    async def test_no_conninfo(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.5)
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        # No response queued → returns None
        guid = await radio._control_phase._receive_guid()
        assert guid is None


class TestSendConninfo:
    @pytest.mark.asyncio
    async def test_sends_conninfo(self) -> None:
        radio = IcomRadio("192.168.1.100", username="test", password="pass")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._token = 0x12345678
        radio._tok_request = 0xABCD
        await radio._control_phase._send_conninfo(b"\x00" * 16)
        assert len(mt.sent_packets) == 1
        assert len(mt.sent_packets[0]) == CONNINFO_SIZE

    @pytest.mark.asyncio
    async def test_sends_conninfo_without_guid(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._token = 0
        radio._tok_request = 0
        await radio._control_phase._send_conninfo(None)
        assert len(mt.sent_packets) == 1

    @pytest.mark.asyncio
    async def test_tx_codec_is_mono_even_when_rx_codec_is_stereo(self) -> None:
        """Issue #794: IC-7610 stock firmware rejects conninfo with stereo
        ``txcodec`` (mic path is mono-only).  Regardless of the requested RX
        codec, the conninfo ``txcodec`` byte at offset 0x73 must be mono
        (PCM_1CH_16BIT = 0x04).
        """
        from rigplane.types import AudioCodec

        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._token = 0x12345678
        radio._tok_request = 0xABCD
        await radio._control_phase._send_conninfo(b"\x00" * 16)
        assert len(mt.sent_packets) == 1
        packet = mt.sent_packets[0]
        # Conninfo layout: rxcodec at byte 0x72, txcodec at byte 0x73
        # (see ``src/rigplane/auth.py`` ``build_conninfo_packet``).
        assert packet[0x72] == int(AudioCodec.PCM_2CH_16BIT), (
            f"rxcodec should reflect requested codec 0x10, got 0x{packet[0x72]:02X}"
        )
        assert packet[0x73] == int(AudioCodec.PCM_1CH_16BIT), (
            f"txcodec must be mono 0x04 even for stereo RX, got 0x{packet[0x73]:02X}"
        )
        rx_sample = struct.unpack_from(">I", packet, 0x74)[0]
        tx_sample = struct.unpack_from(">I", packet, 0x78)[0]
        assert rx_sample == 16000
        assert tx_sample == 16000

    @pytest.mark.asyncio
    async def test_conninfo_honors_explicit_sample_rate_override(self) -> None:
        from rigplane.types import AudioCodec

        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
            audio_sample_rate=48000,
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._token = 0x12345678
        radio._tok_request = 0xABCD

        await radio._control_phase._send_conninfo(b"\x00" * 16)

        packet = mt.sent_packets[0]
        assert packet[0x72] == int(AudioCodec.PCM_2CH_16BIT)
        assert packet[0x73] == int(AudioCodec.PCM_1CH_16BIT)
        assert struct.unpack_from(">I", packet, 0x74)[0] == 48000
        assert struct.unpack_from(">I", packet, 0x78)[0] == 48000


class TestReceiveCivPort:
    @pytest.mark.asyncio
    async def test_receives_civ_port(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=1.0)
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_status(50002, 50003))
        port = await radio._control_phase._receive_civ_port()
        assert port == 50002
        assert radio._audio_port == 50003

    @pytest.mark.asyncio
    async def test_timeout_returns_zero(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.2)
        mt = ConnectMockTransport()
        # Cap receive_packet timeout so the 2.0s deadline loop iterates fast
        _orig_recv = mt.receive_packet

        async def _fast_recv(timeout=5.0):
            return await _orig_recv(timeout=min(timeout, 0.02))

        mt.receive_packet = _fast_recv  # type: ignore[assignment]
        radio._ctrl_transport = mt
        port = await radio._control_phase._receive_civ_port()
        assert port == 0

    @pytest.mark.asyncio
    async def test_skips_non_status(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=1.0)
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        # Queue a non-status packet first, then status
        mt.queue_response(b"\x00" * 0x30)  # wrong size
        mt.queue_response(_build_status(50004))
        port = await radio._control_phase._receive_civ_port()
        assert port == 50004

    @pytest.mark.asyncio
    async def test_two_zero_status_packets_return_quickly(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=5.0)
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_status(0, 50003))
        mt.queue_response(_build_status(0, 50003))

        start = time.monotonic()
        port = await radio._control_phase._receive_civ_port()
        elapsed = time.monotonic() - start

        assert port == 0
        assert radio._audio_port == 50003
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_status_rejection_error_is_recorded(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=1.0)
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_status(0, 50003, error=0xFFFFFFFF))
        port = await radio._control_phase._receive_civ_port()
        assert port == 0
        assert getattr(radio, "_last_status_error", 0) == 0xFFFFFFFF

    def test_status_retry_pause_uses_reject_cooldown(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._last_status_error = 0xFFFFFFFF
        assert (
            radio._control_phase._status_retry_pause()
            == radio._control_phase._STATUS_REJECT_COOLDOWN
        )
        radio._last_status_error = 0
        assert (
            radio._control_phase._status_retry_pause()
            == radio._control_phase._STATUS_RETRY_PAUSE
        )


class TestSendOpenClose:
    @pytest.mark.asyncio
    async def test_open(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._civ_transport = mt
        await radio._send_open_close(open_stream=True)
        assert len(mt.sent_packets) == 1
        pkt = mt.sent_packets[0]
        assert pkt[0x15] == 0x04  # open magic

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._civ_transport = mt
        await radio._send_open_close(open_stream=False)
        assert len(mt.sent_packets) == 1
        pkt = mt.sent_packets[0]
        assert pkt[0x15] == 0x00  # close magic

    @pytest.mark.asyncio
    async def test_noop_without_transport(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._civ_transport = None
        await radio._send_open_close(open_stream=True)  # no error


class TestWaitForPacket:
    @pytest.mark.asyncio
    async def test_returns_correct_size(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=1.0)
        mt = ConnectMockTransport()
        mt.queue_response(b"\x00" * 0x20)  # wrong size
        mt.queue_response(b"\x00" * 0x60)  # correct
        result = await radio._control_phase._wait_for_packet(
            mt, size=0x60, label="test"
        )
        assert len(result) == 0x60

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.1)
        mt = ConnectMockTransport()
        # Cap receive_packet timeout so the 2.0s deadline is reached quickly
        _orig_recv = mt.receive_packet

        async def _fast_recv(timeout=5.0):
            return await _orig_recv(timeout=min(timeout, 0.02))

        mt.receive_packet = _fast_recv  # type: ignore[assignment]
        with pytest.raises(TimeoutError, match="test timed out"):
            await radio._control_phase._wait_for_packet(mt, size=0x60, label="test")

    @pytest.mark.asyncio
    async def test_skips_wrong_sizes(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=1.0)
        mt = ConnectMockTransport()
        for _ in range(5):
            mt.queue_response(b"\x00" * 0x10)
        mt.queue_response(b"\xff" * 0x60)
        result = await radio._control_phase._wait_for_packet(
            mt, size=0x60, label="test"
        )
        assert result == b"\xff" * 0x60


class TestFlushQueue:
    @pytest.mark.asyncio
    async def test_flush_empty(self) -> None:
        mt = ConnectMockTransport()
        count = await IcomRadio._flush_queue(mt)
        assert count == 0

    @pytest.mark.asyncio
    async def test_flush_drains(self) -> None:
        mt = ConnectMockTransport()
        for _ in range(10):
            mt.queue_response(b"\x00" * 16)
        count = await IcomRadio._flush_queue(mt)
        assert count == 10


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._civ_transport = mt
        radio._connected = True
        await radio.disconnect()
        assert not radio.connected
        assert mt.disconnected

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        await radio.disconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_aexit(self) -> None:
        radio = IcomRadio("192.168.1.100")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._civ_transport = mt
        radio._connected = True
        await radio.__aexit__(None, None, None)
        assert not radio.connected


class TestConnectReadiness:
    @pytest.mark.asyncio
    async def test_connect_raises_when_radio_never_becomes_ready(self) -> None:
        radio = IcomRadio("192.168.1.100", username="u", password="p", timeout=0.2)
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        fake_civ_transport = ConnectMockTransport()

        with (
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(radio._control_phase, "_send_conninfo", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(return_value=50002),
            ),
            patch.object(
                radio._control_phase, "_flush_queue", new=AsyncMock(return_value=0)
            ),
            patch.object(radio._control_phase, "_start_token_renewal"),
            patch.object(radio._control_phase, "_start_watchdog"),
            patch.object(radio._civ_runtime, "start_pump"),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
            patch.object(radio._civ_runtime, "start_worker"),
            patch("rigplane.transport.IcomTransport", return_value=fake_civ_transport),
            patch("rigplane._control_phase.asyncio.sleep", new=AsyncMock()),
        ):
            radio._civ_ready_idle_timeout = 0.0
            with pytest.raises(ConnectionError, match="radio connect aborted"):
                await radio.connect()

        assert mt.disconnected is True


class TestConnectSessionRejection:
    @pytest.mark.asyncio
    async def test_connect_raises_on_status_rejection_after_retries(self) -> None:
        radio = IcomRadio("192.168.1.100", username="u", password="p")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        async def _reject_status() -> int:
            radio._last_status_error = 0xFFFFFFFF
            return 0

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(radio._control_phase, "_send_conninfo", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_reject_status),
            ),
        ):
            with pytest.raises(ConnectionError, match="rejected session allocation"):
                await radio.connect()

        assert mt.disconnected is True

    @pytest.mark.asyncio
    async def test_stereo_codec_fallback_succeeds(self) -> None:
        """Stereo rx_codec + 0xFFFFFFFF → retry with mono → connection succeeds."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        civ_port_responses = [0, 50001]
        status_errors = [0xFFFFFFFF, 0]

        async def _status_flow() -> int:
            radio._last_status_error = status_errors.pop(0)
            return civ_port_responses.pop(0)

        codec_per_call: list[AudioCodec] = []

        async def _capture_codec(*_args: object, **_kwargs: object) -> None:
            codec_per_call.append(radio._audio_codec)

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(
                radio._control_phase,
                "_send_conninfo",
                new=AsyncMock(side_effect=_capture_codec),
            ),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_status_flow),
            ),
            patch.object(
                radio._control_phase, "_flush_queue", new=AsyncMock(return_value=0)
            ),
            patch.object(radio._control_phase, "_start_token_renewal"),
            patch.object(radio._control_phase, "_start_watchdog"),
            patch.object(radio._civ_runtime, "start_pump"),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
            patch.object(radio._civ_runtime, "start_worker"),
            patch(
                "rigplane.transport.IcomTransport",
                return_value=ConnectMockTransport(),
            ),
            patch("rigplane._control_phase.asyncio.sleep", new=AsyncMock()),
        ):
            radio._civ_ready_idle_timeout = 0.0
            # Not interested in the connect path after CIV transport; only
            # whether fallback downgraded the codec before the second conninfo.
            try:
                await radio.connect()
            except ConnectionError:
                pass

        assert codec_per_call == [
            AudioCodec.PCM_2CH_16BIT,
            AudioCodec.PCM_1CH_16BIT,
        ], "Second conninfo must be sent with mono codec, before any later mutation"
        assert radio.audio_stream_request.rx_codec == AudioCodec.PCM_2CH_16BIT
        assert radio.audio_stream_contract.rx_codec == AudioCodec.PCM_1CH_16BIT
        assert radio.audio_stream_contract.tx_codec == AudioCodec.PCM_1CH_16BIT
        assert radio.audio_stream_contract.rx_sample_rate_hz == 16000
        assert radio.audio_stream_contract.tx_sample_rate_hz == 16000
        assert radio.audio_stream_contract.rx_codec_source == AudioConfigSource.FALLBACK
        assert (
            radio.audio_stream_contract.fallback_reason == "conninfo-stereo-rx-rejected"
        )
        assert radio._audio_codec == AudioCodec.PCM_1CH_16BIT
        assert radio.audio_codec == AudioCodec.PCM_1CH_16BIT
        assert radio._civ_port == 50001

    @pytest.mark.asyncio
    async def test_stereo_codec_fallback_raises_on_second_rejection(self) -> None:
        """Stereo rx_codec rejected twice → raise, no infinite loop."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        async def _always_reject() -> int:
            radio._last_status_error = 0xFFFFFFFF
            return 0

        send_mock = AsyncMock()

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(radio._control_phase, "_send_conninfo", new=send_mock),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_always_reject),
            ),
        ):
            with pytest.raises(ConnectionError, match="both stereo and mono rx_codec"):
                await radio.connect()

        assert send_mock.await_count == 2, "Exactly one retry — no infinite loop"
        assert mt.disconnected is True

    @pytest.mark.asyncio
    async def test_stereo_fallback_falls_into_busy_retry_when_mono_not_ready(
        self,
    ) -> None:
        """Stereo rejected (0xFFFFFFFF) → mono accepted but civ_port=0 with no
        error flag → enter busy-retry loop with mono; success on second try.

        Regression guard for Codex review finding on PR #802: the initial
        implementation of #797 treated any non-positive civ_port after the mono
        retry as a hard failure, bypassing the existing busy-session retry
        behaviour used when civ_port=0 without 0xFFFFFFFF.  That made real,
        recoverable connects deterministically fail on radios that need a few
        hundred ms of settle time after the conninfo downgrade.
        """
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        # 1st call: stereo rejected (0xFFFFFFFF).
        # 2nd call: mono accepted but still warming (civ_port=0, error=0 — no flag).
        # 3rd call (busy-retry #1): now ready (civ_port > 0).
        civ_port_responses = [0, 0, 50001]
        status_errors = [0xFFFFFFFF, 0x00000000, 0x00000000]

        async def _status_flow() -> int:
            radio._last_status_error = status_errors.pop(0)
            return civ_port_responses.pop(0)

        send_mock = AsyncMock()

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(radio._control_phase, "_send_conninfo", new=send_mock),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_status_flow),
            ),
            patch.object(
                radio._control_phase, "_flush_queue", new=AsyncMock(return_value=0)
            ),
            patch.object(radio._control_phase, "_start_token_renewal"),
            patch.object(radio._control_phase, "_start_watchdog"),
            patch.object(radio._civ_runtime, "start_pump"),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
            patch.object(radio._civ_runtime, "start_worker"),
            patch(
                "rigplane.transport.IcomTransport",
                return_value=ConnectMockTransport(),
            ),
            patch("rigplane._control_phase.asyncio.sleep", new=AsyncMock()),
        ):
            radio._civ_ready_idle_timeout = 0.0
            try:
                await radio.connect()
            except ConnectionError:
                # Swallow downstream CIV transport failures — the test asserts
                # on the control-phase invariants before CIV transport setup.
                pass

        # 1 initial + 1 mono fallback + 1 busy-retry = 3 total conninfo sends.
        assert send_mock.await_count == 3, (
            "Expected 3 conninfo sends (initial + mono fallback + busy-retry #1)"
        )
        assert radio._audio_codec == AudioCodec.PCM_1CH_16BIT, (
            "Codec must stay on mono through busy-retry"
        )
        assert radio._civ_port == 50001

    @pytest.mark.asyncio
    async def test_mono_codec_no_fallback_raises_immediately(self) -> None:
        """Mono rx_codec + 0xFFFFFFFF → raise immediately, no retry attempted."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_1CH_16BIT,
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        async def _reject_status() -> int:
            radio._last_status_error = 0xFFFFFFFF
            return 0

        send_mock = AsyncMock()

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(radio._control_phase, "_send_conninfo", new=send_mock),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_reject_status),
            ),
        ):
            with pytest.raises(ConnectionError, match="rejected session allocation"):
                await radio.connect()

        assert send_mock.await_count == 1, "No retry for mono codec"
        assert mt.disconnected is True


class _FakeSocket:
    def __init__(self, sockname: tuple[str, int]) -> None:
        self.sockname = sockname
        self.bound: tuple[str, int] | None = None
        self.connected: tuple[str, int] | None = None

    def connect(self, addr: tuple[str, int]) -> None:
        self.connected = addr

    def bind(self, addr: tuple[str, int]) -> None:
        self.bound = addr

    def getsockname(self) -> tuple[str, int]:
        return self.sockname

    def close(self) -> None:
        return None


class TestWifiBindBehavior:
    @pytest.mark.asyncio
    async def test_connect_uses_routed_local_bind_host_for_control_and_civ(
        self,
    ) -> None:
        radio = IcomRadio("192.168.2.1", username="u", password="p")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        probe_sock = _FakeSocket(("192.168.2.194", 40000))
        civ_sock = _FakeSocket(("192.168.2.194", 50002))
        audio_sock = _FakeSocket(("192.168.2.194", 50003))

        fake_civ_transport = ConnectMockTransport()
        mt._udp_transport = object()
        fake_civ_transport._udp_transport = object()

        async def _mark_ready(_transport: object) -> int:
            radio._civ_stream_ready = True
            radio._last_civ_data_received = time.monotonic()
            return 0

        with (
            patch(
                "rigplane._control_phase._socket.socket",
                side_effect=[probe_sock, civ_sock, audio_sock],
            ),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(radio._control_phase, "_send_conninfo", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(return_value=50002),
            ),
            patch.object(radio._control_phase, "_start_token_renewal"),
            patch.object(radio._control_phase, "_start_watchdog"),
            patch.object(radio._control_phase, "_send_open_close", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_flush_queue",
                new=AsyncMock(side_effect=_mark_ready),
            ),
            patch.object(radio._civ_runtime, "start_pump"),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
            patch.object(radio._civ_runtime, "start_worker"),
            patch("rigplane.transport.IcomTransport", return_value=fake_civ_transport),
            patch.object(radio, "_fetch_initial_state", new=AsyncMock()),
        ):
            await radio.connect()

        assert probe_sock.connected == ("192.168.2.1", 50001)
        assert civ_sock.bound == ("192.168.2.194", 0)
        assert audio_sock.bound == ("192.168.2.194", 0)
        assert mt.connect_args["local_host"] == "192.168.2.194"
        assert fake_civ_transport.connect_args == {
            "host": "192.168.2.1",
            "port": 50002,
            "local_host": "192.168.2.194",
            "local_port": 50002,
        }

    @pytest.mark.asyncio
    async def test_connect_raises_on_persistent_civ_port_zero_without_error_flag(
        self,
    ) -> None:
        """civ_port=0 after all retries (no 0xFFFFFFFF flag) also raises ConnectionError."""
        radio = IcomRadio("192.168.1.100", username="u", password="p")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        async def _zero_status() -> int:
            # No error flag — just persistent civ_port=0
            radio._last_status_error = 0
            return 0

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(radio._control_phase, "_send_conninfo", new=AsyncMock()),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_zero_status),
            ),
        ):
            with pytest.raises(ConnectionError, match="rejected session allocation"):
                await radio.connect()

        assert mt.disconnected is True
