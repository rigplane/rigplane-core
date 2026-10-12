"""Tests for IcomRadio connect/disconnect lifecycle and internal helpers."""

import asyncio
import contextlib
import struct
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from rigplane.profiles import RadioProfile
from rigplane.runtime._control_phase import ControlPhaseRuntime, _session_reject_hint
from rigplane.runtime.session_lifecycle import (
    AttemptResult,
    CoreRadioSessionLifecycle,
    LifecycleState,
)
from rigplane.transport import ConnectionState
from rigplane.types import AudioCodec

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect
from test_radio import MockTransport
from test_session_lifecycle import FakeMechanism


# ---------------------------------------------------------------------------
# Extended MockTransport for connection tests
# ---------------------------------------------------------------------------


class ConnectMockTransport(MockTransport):
    """Extended mock that simulates the connection handshake."""

    def __init__(self) -> None:
        super().__init__()
        self.state = ConnectionState.DISCONNECTED
        self.connect_count = 0
        self.disconnect_count = 0

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: "object | None" = None,
    ) -> None:
        self.connect_count += 1
        self.connected = True
        self.state = ConnectionState.CONNECTING
        self.connect_args = {
            "host": host,
            "port": port,
            "local_host": local_host,
            "local_port": local_port,
        }

    async def disconnect(self) -> None:
        self.disconnect_count += 1
        await super().disconnect()

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


def _lan_profile_without_codec_preference() -> RadioProfile:
    return RadioProfile(
        id="test-lan",
        model="Test LAN",
        civ_addr=0x98,
        receiver_count=1,
        capabilities=frozenset({"lan"}),
        cmd29_routes=frozenset(),
        has_lan=True,
    )


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


class _VirtualMonotonic:
    """Fake ``time.monotonic`` for the busy-retry budget tests.

    The production loop sizes a busy hold in ``ControlPhaseRuntime``'s
    start-up budget constant; these tests advance the virtual clock by one
    reject cooldown's worth of time per scripted busy exchange so a hold
    spanning minutes plays out in real seconds.
    """

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


#: Virtual seconds one scripted busy exchange advances the clock: slightly
#: more than one ``_STATUS_REJECT_COOLDOWN`` (30 s in production), so four
#: rejections span past the old three-retry ~90 s window.
_REJECT_STEP_S = 35.0


def _busy_hold_patch_stack(
    radio: IcomRadio,
    clock: _VirtualMonotonic,
    *,
    login_responses: list[bytes],
    status_flow: object,
    send_mock: AsyncMock,
) -> ExitStack:
    """Shared fake stack for first-connect busy rows.

    Every attempt logs in with the next scripted token, reports the
    scripted status flow, and records the token each conninfo is sent on;
    the lifecycle cooldown sleeps and both virtual clocks are the shared
    virtual seam. Use as ``with _busy_hold_patch_stack(...)``.
    """
    stack = ExitStack()
    stack.enter_context(
        patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0)
    )
    stack.enter_context(
        patch("rigplane.runtime._control_phase.time.monotonic", new=clock.monotonic)
    )
    stack.enter_context(
        patch(
            "rigplane.runtime.session_lifecycle.time.monotonic",
            new=clock.monotonic,
        )
    )
    stack.enter_context(
        patch(
            "rigplane.runtime.session_lifecycle.asyncio.sleep",
            new=AsyncMock(),
        )
    )
    stack.enter_context(
        patch.object(
            radio._control_phase,
            "_wait_for_packet",
            new=AsyncMock(side_effect=list(login_responses)),
        )
    )
    stack.enter_context(
        patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock())
    )
    stack.enter_context(
        patch.object(
            radio._control_phase,
            "_receive_guid",
            new=AsyncMock(return_value=b"\x00" * 16),
        )
    )
    stack.enter_context(
        patch.object(radio._control_phase, "_send_conninfo", new=send_mock)
    )
    stack.enter_context(
        patch.object(
            radio._control_phase,
            "_receive_civ_port",
            new=AsyncMock(side_effect=status_flow),
        )
    )
    stack.enter_context(
        patch.object(
            radio._control_phase, "_flush_queue", new=AsyncMock(return_value=0)
        )
    )
    stack.enter_context(patch.object(radio._control_phase, "_start_token_renewal"))
    stack.enter_context(patch.object(radio._control_phase, "_start_watchdog"))
    stack.enter_context(patch.object(radio._civ_runtime, "start_pump"))
    stack.enter_context(patch.object(radio._civ_runtime, "start_data_watchdog"))
    stack.enter_context(patch.object(radio._civ_runtime, "start_worker"))
    stack.enter_context(
        patch(
            "rigplane.transport.IcomTransport",
            return_value=ConnectMockTransport(),
        )
    )
    stack.enter_context(
        patch(
            "rigplane.runtime._control_phase.wait_for_radio_startup_ready",
            new=AsyncMock(),
        )
    )
    stack.enter_context(patch.object(radio, "_fetch_initial_state", new=AsyncMock()))
    # MOR-3064 seam: the fake CI-V transport runs no rx pump, so the
    # identity read is answered directly with the IC-7610 token 0x98.
    stack.enter_context(
        patch.object(
            radio,
            "_read_lan_identity_payload",
            new=AsyncMock(return_value=b"\x98"),
        )
    )
    return stack


def _token_remove_tokens(packets: list[bytes]) -> list[int]:
    """Tokens carried by token-remove (magic 0x01) packets on the wire."""
    removes = []
    for pkt in packets:
        assert len(pkt) == 0x40 and pkt[0x15] == 0x01, (
            f"expected only token-remove traffic besides logins, got {len(pkt)} bytes"
        )
        removes.append(struct.unpack_from("<I", pkt, 0x1C)[0])
    return removes


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
        radio = IcomRadio("192.168.1.100", model="IC-7610")
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


class _ConnectOnceStubs:
    """Patches that let _connect_once run without a radio or a real socket.

    The GUID step is left real: the queued 0x90 is what _receive_guid reads.
    """

    def __init__(self, radio: IcomRadio) -> None:
        fake_civ_transport = ConnectMockTransport()
        self._patches = (
            patch.object(
                radio._control_phase,
                "_resolve_local_bind_host",
                return_value="127.0.0.1",
            ),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
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
            patch.object(radio._civ_runtime, "advance_generation"),
            patch.object(radio._civ_runtime, "start_pump"),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
            patch.object(radio._civ_runtime, "start_worker"),
            patch("rigplane.transport.IcomTransport", return_value=fake_civ_transport),
            patch("rigplane._control_phase.asyncio.sleep", new=AsyncMock()),
            patch(
                "rigplane._control_phase.wait_for_radio_startup_ready",
                new=AsyncMock(),
            ),
        )

    def __enter__(self) -> "_ConnectOnceStubs":
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for item in reversed(self._patches):
            item.stop()


class TestConninfoNoticeWiring:
    @pytest.mark.asyncio
    async def test_setup_keeps_the_notice_callback_clear_so_guid_is_queued(
        self,
    ) -> None:
        """During setup the callback is clear and _receive_guid reads the 0x90."""
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._ctrl_transport._conninfo_notice_callback = (
            radio._control_phase._on_conninfo_notice
        )
        seen: list[bool] = []
        real_receive_guid = radio._control_phase._receive_guid

        async def spy_receive_guid() -> bytes | None:
            seen.append(radio._ctrl_transport._conninfo_notice_callback is None)
            return await real_receive_guid()

        mt.queue_response(_build_conninfo())
        with (
            _ConnectOnceStubs(radio),
            patch.object(
                radio._control_phase, "_receive_guid", side_effect=spy_receive_guid
            ),
        ):
            await radio._control_phase._connect_once()
        assert seen == [True]

    @pytest.mark.asyncio
    async def test_after_setup_the_callback_is_the_notice_handler(self) -> None:
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_conninfo())
        seen: list[object] = []

        def record(_self: object, callback: object) -> None:
            seen.append(callback)

        with (
            _ConnectOnceStubs(radio),
            patch.object(
                type(mt),
                "_conninfo_notice_callback",
                property(lambda self: None, record),
                create=True,
            ),
        ):
            await radio._control_phase._connect_once()
        assert seen[-1] == radio._control_phase._on_conninfo_notice

    def test_busy_zero_requests_recovery(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        radio._civ_runtime.request_recovery_now = MagicMock()
        pkt = bytearray(_build_conninfo())
        struct.pack_into("<I", pkt, 0x60, 0)
        radio._control_phase._on_conninfo_notice(bytes(pkt))
        radio._civ_runtime.request_recovery_now.assert_called_once_with(
            "radio reported the LAN session free (conninfo busy=0)"
        )

    def test_busy_one_does_not_request_recovery(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        radio._civ_runtime.request_recovery_now = MagicMock()
        pkt = bytearray(_build_conninfo())
        struct.pack_into("<I", pkt, 0x60, 1)
        radio._control_phase._on_conninfo_notice(bytes(pkt))
        radio._civ_runtime.request_recovery_now.assert_not_called()


class TestReceiveGuid:
    @pytest.mark.asyncio
    async def test_receives_guid(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_conninfo())
        guid = await radio._control_phase._receive_guid()
        assert guid is not None
        assert len(guid) == 16

    @pytest.mark.asyncio
    async def test_no_conninfo(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.5, model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        # No response queued → returns None
        guid = await radio._control_phase._receive_guid()
        assert guid is None


def _conninfo_radio_name(packet: bytes) -> bytes:
    """The conninfo radio-name field — 32 ASCII bytes at 0x40, NUL-padded.

    Offset and width per ``core/auth.py: build_conninfo_packet``.
    """
    return packet[0x40:0x60].rstrip(b"\x00")


class _NamelessControlPhaseHost:
    """Control-phase host with no ``model`` attribute at all.

    Carries only what ``ControlPhaseRuntime._send_conninfo`` reads.
    """

    def __init__(self) -> None:
        self._ctrl_transport = ConnectMockTransport()
        self._username = "user"
        self._token = 0x12345678
        self._tok_request = 0xABCD
        self._auth_seq = 0
        self._audio_stream_contract = SimpleNamespace(
            rx_codec=AudioCodec.PCM_1CH_16BIT,
            tx_codec=AudioCodec.PCM_1CH_16BIT,
            rx_sample_rate_hz=48000,
            tx_sample_rate_hz=48000,
        )


class TestSendConninfo:
    @pytest.mark.asyncio
    async def test_sends_conninfo(self) -> None:
        radio = IcomRadio(
            "192.168.1.100", username="test", password="pass", model="IC-7610"
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._token = 0x12345678
        radio._tok_request = 0xABCD
        await radio._control_phase._send_conninfo(b"\x00" * 16)
        assert len(mt.sent_packets) == 1
        assert len(mt.sent_packets[0]) == CONNINFO_SIZE

    @pytest.mark.asyncio
    async def test_conninfo_carries_the_radios_own_model(self) -> None:
        """The name field at 0x40 is whatever the rig calls itself."""
        radio = IcomRadio("192.168.1.100", model="IC-7300")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        await radio._control_phase._send_conninfo(b"\x00" * 16)
        assert _conninfo_radio_name(mt.sent_packets[0]) == b"IC-7300"

    @pytest.mark.asyncio
    async def test_conninfo_names_no_radio_when_the_host_names_none(self) -> None:
        """R59/MOR-2425: an unnamed host puts no rig name on the wire.

        The default here used to be ``"IC-7610"``. ``ControlPhaseHost``
        (``runtime/_runtime_protocols.py``) does not declare ``model``, so
        ``_send_conninfo`` reaches for it with ``getattr``.
        """
        host = _NamelessControlPhaseHost()
        await ControlPhaseRuntime(host)._send_conninfo(b"\x00" * 16)  # type: ignore[arg-type]
        packet = host._ctrl_transport.sent_packets[0]
        assert _conninfo_radio_name(packet) == b"unspecified"

    @pytest.mark.asyncio
    async def test_sends_conninfo_without_guid(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
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
            profile=_lan_profile_without_codec_preference(),
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
        assert rx_sample == 48000
        assert tx_sample == 48000

    @pytest.mark.asyncio
    async def test_conninfo_honors_explicit_sample_rate_override(self) -> None:
        from rigplane.types import AudioCodec

        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
            audio_sample_rate=48000,
            model="IC-7610",
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
        radio = IcomRadio("192.168.1.100", timeout=1.0, model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_status(50002, 50003))
        port = await radio._control_phase._receive_civ_port()
        assert port == 50002
        assert radio._audio_port == 50003

    @pytest.mark.asyncio
    async def test_timeout_returns_zero(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.2, model="IC-7610")
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
        radio = IcomRadio("192.168.1.100", timeout=1.0, model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        # Queue a non-status packet first, then status
        mt.queue_response(b"\x00" * 0x30)  # wrong size
        mt.queue_response(_build_status(50004))
        port = await radio._control_phase._receive_civ_port()
        assert port == 50004

    @pytest.mark.asyncio
    async def test_two_zero_status_packets_return_quickly(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=5.0, model="IC-7610")
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
        radio = IcomRadio("192.168.1.100", timeout=1.0, model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_status(0, 50003, error=0xFFFFFFFF))
        port = await radio._control_phase._receive_civ_port()
        assert port == 0
        assert getattr(radio, "_last_status_error", 0) == 0xFFFFFFFF

    def test_status_retry_pause_uses_reject_cooldown(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
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
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        mt = ConnectMockTransport()
        radio._civ_transport = mt
        await radio._send_open_close(open_stream=True)
        assert len(mt.sent_packets) == 1
        pkt = mt.sent_packets[0]
        assert pkt[0x15] == 0x04  # open magic

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        mt = ConnectMockTransport()
        radio._civ_transport = mt
        await radio._send_open_close(open_stream=False)
        assert len(mt.sent_packets) == 1
        pkt = mt.sent_packets[0]
        assert pkt[0x15] == 0x00  # close magic

    @pytest.mark.asyncio
    async def test_noop_without_transport(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        radio._civ_transport = None
        await radio._send_open_close(open_stream=True)  # no error


class TestWaitForPacket:
    @pytest.mark.asyncio
    async def test_returns_correct_size(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=1.0, model="IC-7610")
        mt = ConnectMockTransport()
        mt.queue_response(b"\x00" * 0x20)  # wrong size
        mt.queue_response(b"\x00" * 0x60)  # correct
        result = await radio._control_phase._wait_for_packet(
            mt, size=0x60, label="test"
        )
        assert len(result) == 0x60

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.1, model="IC-7610")
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
        radio = IcomRadio("192.168.1.100", timeout=1.0, model="IC-7610")
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
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._civ_transport = mt
        radio._connected = True
        await radio.disconnect()
        assert not radio.connected
        assert mt.disconnected

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        await radio.disconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_aexit(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        radio._civ_transport = mt
        radio._connected = True
        await radio.__aexit__(None, None, None)
        assert not radio.connected


class TestConnectReadiness:
    @pytest.mark.asyncio
    async def test_connect_raises_when_radio_never_becomes_ready(self) -> None:
        radio = IcomRadio(
            "192.168.1.100", username="u", password="p", timeout=0.2, model="IC-7610"
        )
        mt = ConnectMockTransport()
        mt._udp_transport = MagicMock()
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
            patch.object(
                radio, "_read_lan_identity_payload", new=AsyncMock(return_value=b"\x98")
            ),
        ):
            radio._civ_ready_idle_timeout = 0.0
            with pytest.raises(ConnectionError, match="radio connect aborted"):
                await radio.connect()

        assert mt.disconnected is True


class TestConnectSessionRejection:
    @pytest.mark.asyncio
    async def test_persistent_busy_rejection_raises_never_ready(self) -> None:
        """Persistent first-connect busy rejects end in the resident
        never-ready error (MOR-3391): each reject is released and the retry
        logs in again instead of spinning conninfo on the rejected token.

        The part-1b live-holder stop is disabled here (threshold ``None``):
        with the production wiring a persistent busy stops at the 90 s
        live-holder error first; this row pins the 600 s outer cap.
        """
        with patch.object(
            ControlPhaseRuntime, "STARTUP_BUSY_LIVE_HOLDER_AFTER_S", None
        ):
            radio = IcomRadio(
                "192.168.1.100", username="u", password="p", model="IC-7610"
            )
            mt = ConnectMockTransport()
            radio._ctrl_transport = mt
            clock = _VirtualMonotonic()
            logins = [_build_login_response(token=0x4000 + i) for i in range(40)]

            async def _reject_status() -> int:
                clock.advance(_REJECT_STEP_S)
                radio._last_status_error = 0xFFFFFFFF
                return 0

            with _busy_hold_patch_stack(
                radio,
                clock,
                login_responses=logins,
                status_flow=_reject_status,
                send_mock=AsyncMock(),
            ):
                with pytest.raises(ConnectionError, match="never became ready"):
                    await radio.connect()

        # More than one fresh login went out: the retry re-logs in instead
        # of re-sending conninfo on the rejected token.
        login_sends = sum(1 for p in mt.sent_packets if len(p) == 0x80)
        assert login_sends > 1
        assert mt.disconnected is True

    @pytest.mark.asyncio
    async def test_data_port_discovery_timeout_retries_and_closes_pending_sockets(
        self,
    ) -> None:
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        class TimeoutCivTransport(ConnectMockTransport):
            async def connect(
                self,
                host: str,
                port: int,
                *,
                local_host: str | None = None,
                local_port: int = 0,
                sock: object | None = None,
            ) -> None:
                await super().connect(
                    host,
                    port,
                    local_host=local_host,
                    local_port=local_port,
                    sock=sock,
                )
                raise TimeoutError(
                    "Radio did not respond to discovery after 10 attempts"
                )

        first_civ_sock = _FakeSocket(("192.168.2.194", 50002))
        first_audio_sock = _FakeSocket(("192.168.2.194", 50003))
        second_civ_sock = _FakeSocket(("192.168.2.194", 50004))
        second_audio_sock = _FakeSocket(("192.168.2.194", 50005))
        timeout_civ_transport = TimeoutCivTransport()
        success_civ_transport = ConnectMockTransport()

        with (
            patch.object(
                radio._control_phase,
                "_resolve_local_bind_host",
                return_value="192.168.2.194",
            ),
            patch(
                "rigplane._control_phase._socket.socket",
                side_effect=[
                    first_civ_sock,
                    first_audio_sock,
                    second_civ_sock,
                    second_audio_sock,
                ],
            ),
            patch.object(
                radio._control_phase,
                "_status_retry_pause",
                return_value=0.0,
            ),
            patch.object(
                radio._control_phase,
                "_wait_for_packet",
                new=AsyncMock(return_value=_build_login_response()),
            ),
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=b"\x00" * 16),
            ),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=[50002, 50002]),
            ),
            patch.object(radio._control_phase, "_start_token_renewal"),
            patch.object(radio._control_phase, "_start_watchdog"),
            patch.object(radio._civ_runtime, "start_pump"),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
            patch.object(radio._civ_runtime, "start_worker"),
            patch(
                "rigplane.transport.IcomTransport",
                side_effect=[timeout_civ_transport, success_civ_transport],
            ),
            patch(
                "rigplane.runtime._control_phase.wait_for_radio_startup_ready",
                new=AsyncMock(),
            ),
            patch("rigplane._control_phase.asyncio.sleep", new=AsyncMock()),
            patch.object(radio, "_fetch_initial_state", new=AsyncMock()),
            # The successful attempt's fake CI-V transport has no rx pump
            # (start_pump is stubbed above), so the MOR-3064 ``19 00``
            # identity read is answered directly with the IC-7610 token
            # 0x98 (rigs/ic7610.toml ``[identity]``); the gate verdict
            # stays real.
            patch.object(
                radio,
                "_read_lan_identity_payload",
                new=AsyncMock(return_value=b"\x98"),
            ),
        ):
            # Routed through the unified lifecycle (A3): a data-port discovery
            # timeout surfaces as SESSION_NOT_READY → the resident runner
            # RELEASES the partial attempt, then retries CONNECTING in-process.
            # The second attempt succeeds.
            await radio.connect()

        # Two control connects (one per CONNECTING attempt).
        assert mt.connect_count == 2
        # The timed-out attempt's CI-V transport was torn down by the data-port
        # cooldown cleanup.
        assert timeout_civ_transport.disconnect_count == 1
        # Sockets consumed by asyncio (civ) are not double-closed; the unused
        # audio socket from the timed-out attempt IS closed exactly once.
        assert first_civ_sock.close_count == 0
        assert first_audio_sock.close_count == 1
        # The successful attempt's sockets are not closed (still in use).
        assert second_civ_sock.close_count == 0
        assert second_audio_sock.close_count == 0
        assert radio._civ_transport is success_civ_transport
        assert radio._civ_sock_pending is None
        assert radio._audio_sock_pending is second_audio_sock

        await radio.disconnect()

    @pytest.mark.asyncio
    async def test_stereo_codec_fallback_succeeds(self) -> None:
        """Stereo rx_codec + 0xFFFFFFFF → retry with mono → connection succeeds."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
            profile=_lan_profile_without_codec_preference(),
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
        assert radio.audio_stream_contract.rx_sample_rate_hz == 48000
        assert radio.audio_stream_contract.tx_sample_rate_hz == 48000
        assert radio.audio_stream_contract.rx_codec_source == AudioConfigSource.FALLBACK
        assert (
            radio.audio_stream_contract.fallback_reason == "conninfo-stereo-rx-rejected"
        )
        assert radio._audio_codec == AudioCodec.PCM_1CH_16BIT
        assert radio.audio_codec == AudioCodec.PCM_1CH_16BIT
        assert radio._civ_port == 50001

    @pytest.mark.asyncio
    async def test_explicit_stereo_codec_rejection_does_not_fallback(self) -> None:
        """Explicit probe-style codec requests must preserve candidate evidence."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
            audio_codec_explicit=True,
            model="IC-7610",
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

        assert send_mock.await_count == 1
        assert radio.audio_stream_contract.rx_codec == AudioCodec.PCM_2CH_16BIT
        assert radio.audio_stream_contract.rx_codec_source == AudioConfigSource.EXPLICIT
        assert mt.disconnected is True

    @pytest.mark.asyncio
    async def test_profile_stereo_rejection_uses_busy_retry_not_mono_fallback(
        self,
    ) -> None:
        """Profile-selected stereo codecs are intentional; 0xFFFFFFFF is treated
        as a busy/held session rather than proof that stereo is unsupported.
        """
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            # Default profile is IC-7610, whose stereo codec is profile-selected.
            audio_codec=AudioCodec.PCM_2CH_16BIT,
            model="IC-7610",
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt

        civ_port_responses = [0, 50001]
        status_errors = [0xFFFFFFFF, 0x00000000]

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
            try:
                await radio.connect()
            except ConnectionError:
                pass

        assert codec_per_call == [
            AudioCodec.PCM_2CH_16BIT,
            AudioCodec.PCM_2CH_16BIT,
        ]
        assert radio.audio_stream_contract.rx_codec == AudioCodec.PCM_2CH_16BIT
        assert (
            radio.audio_stream_contract.rx_codec_source
            == AudioConfigSource.PROFILE_DEFAULT
        )
        assert radio._civ_port == 50001

    @pytest.mark.asyncio
    async def test_stereo_codec_fallback_raises_on_second_rejection(self) -> None:
        """Stereo rx_codec rejected twice → raise, no infinite loop."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
            profile=_lan_profile_without_codec_preference(),
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
            profile=_lan_profile_without_codec_preference(),
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
            model="IC-7610",
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


class TestStartupBusyHoldBudget:
    """MOR-3391: a start-up busy reject re-logs in with a FRESH token.

    A web server killed with ``kill -9`` leaves its LAN session held by
    the radio; a restart 1 s later is busy-rejected (``civ_port=0,
    error=0xFFFFFFFF``) until the hold expires. Bench (2026-10-12): a
    hold bounded at (70.7, 85.9] s with separate client ports; a token
    issued during the hold is unusable afterwards (conninfo and renewals
    on it get ``0xFFFFFFFF``), while a fresh login + conninfo after a
    release was granted in 0.85 s.
    So each busy reject RELEASES the busy-issued token and the retry logs
    in again — conninfo is never re-sent on the rejected token — until the
    live-holder stop (part 1b) or the 600 s start-up cap, after which
    start-up ends in the resident ConnectionError.
    """

    @pytest.mark.asyncio
    async def test_first_busy_releases_token_and_relogs_in_with_fresh_token(
        self,
    ) -> None:
        """Row 1: two busy rejects (~70 virtual seconds) then acceptance.

        The hold ends between attempts, before the part-1b live-holder
        stop. Each busy reject must RELEASE the busy-issued token
        (token-remove on the wire) and the next attempt must log in again:
        conninfo is sent exactly once per attempt, on that attempt's fresh
        token, never re-sent on a busy-rejected one.
        """
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()
        tokens = [0x1000 + i for i in range(8)]
        logins = [_build_login_response(token=t) for t in tokens]
        busy_left = [2]
        conninfo_tokens: list[int] = []

        async def _status_flow() -> int:
            if busy_left[0] > 0:
                busy_left[0] -= 1
                clock.advance(_REJECT_STEP_S)
                radio._last_status_error = 0xFFFFFFFF
                return 0
            radio._last_status_error = 0
            return 50001

        async def _capture_conninfo(*_args: object, **_kwargs: object) -> None:
            conninfo_tokens.append(radio._token)

        send_mock = AsyncMock(side_effect=_capture_conninfo)
        with _busy_hold_patch_stack(
            radio,
            clock,
            login_responses=logins,
            status_flow=_status_flow,
            send_mock=send_mock,
        ):
            await radio.connect()

        assert radio._civ_port == 50001
        # Three attempts: one conninfo each, every one on a fresh token.
        assert conninfo_tokens == tokens[:3]
        # On the wire: login (0x80) / token-remove (0x40) strict alternation
        # ending with the granting login — every busy-issued token is
        # removed BEFORE the next login, none lingers beside it.
        kinds = ["login" if len(p) == 0x80 else "remove" for p in mt.sent_packets]
        assert kinds == ["login", "remove"] * 2 + ["login"]
        removes = _token_remove_tokens([p for p in mt.sent_packets if len(p) == 0x40])
        assert removes == tokens[:2]

    @pytest.mark.asyncio
    async def test_first_busy_hold_beyond_the_cap_raises_never_ready(self) -> None:
        """Row 2: a hold that outlives the 600 s cap ends in the resident
        never-ready ConnectionError — not a spin, and not the old
        inline-loop "after retries" error. Every attempt's token is
        released, so no busy-issued token lingers.

        The part-1b live-holder stop is disabled here (threshold ``None``):
        with the production wiring a persistent busy stops at the 90 s
        live-holder error first, so the 600 s raise below pins the outer
        cap as the backstop for lifecycles constructed without it.
        """
        with patch.object(
            ControlPhaseRuntime, "STARTUP_BUSY_LIVE_HOLDER_AFTER_S", None
        ):
            radio = IcomRadio(
                "192.168.1.100", username="u", password="p", model="IC-7610"
            )
            mt = ConnectMockTransport()
            radio._ctrl_transport = mt
            clock = _VirtualMonotonic()
            tokens = [0x2000 + i for i in range(40)]
            logins = [_build_login_response(token=t) for t in tokens]
            conninfo_tokens: list[int] = []

            async def _reject_status() -> int:
                clock.advance(_REJECT_STEP_S)
                radio._last_status_error = 0xFFFFFFFF
                return 0

            async def _capture_conninfo(*_args: object, **_kwargs: object) -> None:
                conninfo_tokens.append(radio._token)

            send_mock = AsyncMock(side_effect=_capture_conninfo)
            with _busy_hold_patch_stack(
                radio,
                clock,
                login_responses=logins,
                status_flow=_reject_status,
                send_mock=send_mock,
            ):
                with pytest.raises(ConnectionError, match="never became ready"):
                    await radio.connect()

        # 35 s virtual steps from the first busy sample at +35 s against the
        # 600 s cap: attempts at 1035..1665, i.e. 19 fresh logins.
        assert conninfo_tokens == tokens[:19]
        assert len(conninfo_tokens) == len(set(conninfo_tokens))
        kinds = ["login" if len(p) == 0x80 else "remove" for p in mt.sent_packets]
        assert kinds == ["login", "remove"] * 19
        removes = _token_remove_tokens([p for p in mt.sent_packets if len(p) == 0x40])
        assert removes == tokens[:19]
        assert mt.disconnected is True

    def test_startup_busy_hold_budget_is_600_s(self) -> None:
        """Row 3: the first-connect busy cap is 600 s sizing the lifecycle's
        fresh-login retry loop (bench 2026-10-12: a hold bounded at
        (70.7, 85.9] s with separate client ports)."""
        assert ControlPhaseRuntime.STARTUP_BUSY_HOLD_BUDGET_S == 600.0

    @pytest.mark.asyncio
    async def test_busy_retry_logs_attempt_wait_and_cap(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Row 4: one status line per busy retry naming the attempt, the
        next wait and the 600 s cap."""
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()
        tokens = [0x3000 + i for i in range(4)]
        logins = [_build_login_response(token=t) for t in tokens]
        busy_left = [2]

        async def _status_flow() -> int:
            if busy_left[0] > 0:
                busy_left[0] -= 1
                clock.advance(_REJECT_STEP_S)
                radio._last_status_error = 0xFFFFFFFF
                return 0
            radio._last_status_error = 0
            return 50001

        send_mock = AsyncMock()
        with _busy_hold_patch_stack(
            radio,
            clock,
            login_responses=logins,
            status_flow=_status_flow,
            send_mock=send_mock,
        ):
            with caplog.at_level(
                "WARNING", logger="rigplane.runtime.session_lifecycle"
            ):
                await radio.connect()

        lines = [
            rec
            for rec in caplog.records
            if rec.name == "rigplane.runtime.session_lifecycle"
            and "fresh login" in rec.getMessage()
        ]
        assert len(lines) == 2
        for i, rec in enumerate(lines, start=1):
            message = rec.getMessage()
            assert f"attempt {i}" in message
            assert "30" in message
            assert "600" in message

    @pytest.mark.asyncio
    async def test_mono_rejection_still_raises_immediately(self) -> None:
        """Row 3 (control): a mono rejection never enters the budget loop."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_1CH_16BIT,
            model="IC-7610",
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()
        send_mock = AsyncMock()

        async def _reject_status() -> int:
            radio._last_status_error = 0xFFFFFFFF
            return 0

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch(
                "rigplane.runtime._control_phase.time.monotonic", new=clock.monotonic
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
            patch.object(radio._control_phase, "_send_conninfo", new=send_mock),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_reject_status),
            ),
        ):
            with pytest.raises(ConnectionError, match="rejected session allocation"):
                await radio.connect()

        assert send_mock.await_count == 1
        assert mt.disconnected is True

    @pytest.mark.asyncio
    async def test_reconnect_busy_rejects_keep_mains_three_tries(self) -> None:
        """Row 4: a reconnect keeps main's fixed three busy attempts.

        The fresh-login budget is a start-up-only scope. ``_connect_once``
        also serves the resident reconnect loop and lifecycle recovery,
        where ``_has_connected_once`` is already set; those callers keep
        main's fixed three ~30 s attempts instead of entering the 600 s
        fresh-login retry on every busy attempt.
        """
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        radio._has_connected_once = True
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()
        send_mock = AsyncMock()

        async def _reject_status() -> int:
            clock.advance(_REJECT_STEP_S)
            radio._last_status_error = 0xFFFFFFFF
            return 0

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch(
                "rigplane.runtime._control_phase.time.monotonic", new=clock.monotonic
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
            patch.object(radio._control_phase, "_send_conninfo", new=send_mock),
            patch.object(
                radio._control_phase,
                "_receive_civ_port",
                new=AsyncMock(side_effect=_reject_status),
            ),
        ):
            with pytest.raises(ConnectionError, match="after retries"):
                await radio.connect()

        # Initial conninfo plus main's three retries: the budget must not
        # add a fourth window to a reconnect.
        assert send_mock.await_count == 4
        assert mt.disconnected is True


class TestStartupBusyLiveHolder:
    """MOR-3391 part 1b: stop the start-up busy retry on a proven LIVE holder.

    Production path: two real ``IcomRadio`` clients against
    ``MockIcomRadio`` in single-owner mode, with the 90 s threshold / 30 s
    cadence scaled to 1.5 s / 0.5 s — the 3:1 ratio is what the rule pins,
    so attempts start at about +0/+0.5/+1.0/+1.5 s after the first busy.
    """

    @staticmethod
    def _client_args(sim: MockIcomRadio) -> dict[str, object]:
        return {
            "host": "127.0.0.1",
            "port": sim.control_port,
            "username": "testuser",
            "password": "testpass",
            "timeout": 2.0,
            "model": "IC-7610",
        }

    @staticmethod
    def _silence_owner(owner: IcomRadio) -> None:
        """Stop the owner's keepalive senders without releasing its session.

        One final ping fixes the hold expiry to now, then cancelling the
        control ping loop, token renewal and watchdog leaves the radio
        holding the owner's session until the simulator's keepalive timer
        expires — a dead client that went silent.
        """
        owner._ctrl_transport._send_ping()
        ping_task = owner._ctrl_transport._ping_task
        if ping_task is not None and not ping_task.done():
            ping_task.cancel()
        owner._control_phase._stop_token_renewal()
        owner._control_phase._stop_watchdog()

    @pytest.mark.asyncio
    async def test_live_owner_second_client_stops_after_four_logins(self) -> None:
        """A live owner: busy at +0/+0.5/+1.0, then the +1.5 s attempt stops.

        The second client raises the honest error after exactly four logins
        and never logs in again; the owner still holds its session.
        """
        sim = MockIcomRadio(single_owner=True, keepalive_hold_s=30.0)
        await sim.start()
        owner = IcomRadio(**self._client_args(sim))  # type: ignore[arg-type]
        second: IcomRadio | None = None
        try:
            with fast_connect():
                await owner.connect()
            assert sim.session_held
            first_owner = sim.owner
            logins_before = sim.login_count
            busy_before = sim.busy_rejects
            with (
                patch.object(ControlPhaseRuntime, "_STATUS_REJECT_COOLDOWN", 0.5),
                patch.object(
                    ControlPhaseRuntime, "STARTUP_BUSY_LIVE_HOLDER_AFTER_S", 1.5
                ),
            ):
                second = IcomRadio(**self._client_args(sim))  # type: ignore[arg-type]
                with fast_connect():
                    with pytest.raises(ConnectionError) as excinfo:
                        await second.connect()

            message = str(excinfo.value)
            assert "radio busy: another client holds the session" in message
            assert "from this computer" in message
            assert "a foreign client may hold it" not in message
            assert sim.login_count - logins_before == 4
            assert sim.busy_rejects - busy_before == 4
            # None after the stop: past one scaled cooldown, counts frozen.
            await asyncio.sleep(0.6)
            assert sim.login_count - logins_before == 4
            assert sim.busy_rejects - busy_before == 4
            assert sim.session_held
            assert sim.owner == first_owner
            assert owner.connected
        finally:
            with contextlib.suppress(Exception):
                await owner.disconnect()
            if second is not None:
                with contextlib.suppress(Exception):
                    await second.disconnect()
            await sim.stop()

    @pytest.mark.asyncio
    async def test_dead_hold_expiry_grants_before_the_threshold(self) -> None:
        """A silent dead client: busy once, then the hold expires and grants.

        The owner's hold (0.5 s from its last ping) expires between the
        second client's attempts (0.5 s cadence, 1.5 s threshold), so the
        second attempt is granted and the client connects under a new owner.
        """
        sim = MockIcomRadio(single_owner=True, keepalive_hold_s=0.5)
        await sim.start()
        owner = IcomRadio(**self._client_args(sim))  # type: ignore[arg-type]
        second: IcomRadio | None = None
        try:
            with fast_connect():
                await owner.connect()
            assert sim.session_held
            first_owner = sim.owner
            self._silence_owner(owner)
            logins_before = sim.login_count
            busy_before = sim.busy_rejects
            with (
                patch.object(ControlPhaseRuntime, "_STATUS_REJECT_COOLDOWN", 0.5),
                patch.object(
                    ControlPhaseRuntime, "STARTUP_BUSY_LIVE_HOLDER_AFTER_S", 1.5
                ),
            ):
                second = IcomRadio(**self._client_args(sim))  # type: ignore[arg-type]
                with fast_connect():
                    await second.connect()

            assert second.connected
            assert sim.login_count - logins_before == 2
            assert sim.busy_rejects - busy_before == 1
            assert sim.session_held
            assert sim.owner is not None and sim.owner != first_owner
        finally:
            with contextlib.suppress(Exception):
                await owner.disconnect()
            if second is not None:
                with contextlib.suppress(Exception):
                    await second.disconnect()
            await sim.stop()


class TestSessionRejectHint:
    """MOR-2630: rejection messages must say what the error code means."""

    def test_same_computer_hint(self) -> None:
        hint = _session_reject_hint(0xFFFFFFFF)
        assert "from this computer" in hint
        assert "One RigPlane server per radio" in hint

    def test_other_computer_hint(self) -> None:
        hint = _session_reject_hint(0xFDFFFFFF)
        assert "another computer" in hint

    def test_unknown_error_hint_is_generic(self) -> None:
        hint = _session_reject_hint(0x00000000)
        assert "this computer" not in hint
        assert "another computer" not in hint

    @pytest.mark.asyncio
    async def test_mono_rejection_message_names_same_computer(self) -> None:
        """Mono rx_codec + 0xFFFFFFFF → the raised message explains the code."""
        radio = IcomRadio(
            "192.168.1.100",
            username="u",
            password="p",
            audio_codec=AudioCodec.PCM_1CH_16BIT,
            model="IC-7610",
        )
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()

        async def _reject_status() -> int:
            clock.advance(_REJECT_STEP_S)
            radio._last_status_error = 0xFFFFFFFF
            return 0

        with (
            patch.object(radio._control_phase, "_status_retry_pause", return_value=0.0),
            patch(
                "rigplane.runtime._control_phase.time.monotonic", new=clock.monotonic
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
                new=AsyncMock(side_effect=_reject_status),
            ),
        ):
            with pytest.raises(ConnectionError) as excinfo:
                await radio.connect()

        message = str(excinfo.value)
        assert message.startswith("Radio rejected session allocation")
        assert "from this computer" in message
        assert "Wait 30-60s" not in message
        assert mt.disconnected is True

    @pytest.mark.asyncio
    async def test_persistent_busy_stops_with_live_holder_message(self) -> None:
        """DEFAULT codec + persistent 0xFFFFFFFF → the part-1b live-holder
        stop: attempts start at +0/+35/+70/+105 virtual seconds, so the
        fourth (at or after first-busy + 90 s) raises the honest error
        instead of retrying into the 600 s budget. On an IC-7610 the held
        session is from this computer, so the error must not claim a
        foreign client holds it."""
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()
        tokens = [0x4000 + i for i in range(40)]
        logins = [_build_login_response(token=t) for t in tokens]
        send_mock = AsyncMock()

        async def _reject_status() -> int:
            clock.advance(_REJECT_STEP_S)
            radio._last_status_error = 0xFFFFFFFF
            return 0

        with _busy_hold_patch_stack(
            radio,
            clock,
            login_responses=logins,
            status_flow=_reject_status,
            send_mock=send_mock,
        ):
            with pytest.raises(ConnectionError) as excinfo:
                await radio.connect()

        message = str(excinfo.value)
        assert "another client holds the session" in message
        assert "from this computer" in message
        assert "One RigPlane server per radio" in message
        assert "a foreign client may hold it" not in message
        # Exactly four logins — one conninfo per attempt, none after the stop.
        assert send_mock.await_count == 4

        assert mt.disconnected is True


class TestStartupBusyHoldSignal:
    """MOR-3391 part 2: the public busy-hold surface on the radio.

    The start-up gate in ``cli`` and the web wire read the hold through
    ``startup_busy_hold_event`` / ``startup_busy_hold_retry_in_seconds``
    instead of the private session lifecycle: the signal is set while a
    first-connect busy cooldown owns the loop, never on a reconnect, and
    clears when the grant lands.
    """

    @pytest.mark.asyncio
    async def test_busy_signal_set_during_first_connect_hold(self) -> None:
        """Two busy rejects then acceptance: the signal owns the hold."""

        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()
        tokens = [0x5000 + i for i in range(4)]
        logins = [_build_login_response(token=t) for t in tokens]
        busy_left = [2]
        observed: list[tuple[bool, object]] = []

        async def _status_flow() -> int:
            observed.append(
                (
                    radio.startup_busy_hold_event.is_set(),
                    radio.startup_busy_hold_retry_in_seconds,
                )
            )
            if busy_left[0] > 0:
                busy_left[0] -= 1
                clock.advance(_REJECT_STEP_S)
                radio._last_status_error = 0xFFFFFFFF
                return 0
            radio._last_status_error = 0
            return 50001

        send_mock = AsyncMock()
        with _busy_hold_patch_stack(
            radio,
            clock,
            login_responses=logins,
            status_flow=_status_flow,
            send_mock=send_mock,
        ):
            await radio.connect()

        assert radio._civ_port == 50001
        # The first sample runs before any reject (no hold yet); every
        # sample after a reject runs inside the busy cooldown.
        assert observed[0] == (False, None)
        assert len(observed) == 3
        for held, retry in observed[1:]:
            assert held is True
            assert isinstance(retry, (int, float)) and not isinstance(retry, bool)
            assert retry >= 0
        # The grant clears the signal and the retry number with it.
        assert radio.startup_busy_hold_event.is_set() is False
        assert radio.startup_busy_hold_retry_in_seconds is None

    @pytest.mark.asyncio
    async def test_reconnect_busy_reject_never_sets_the_busy_signal(self) -> None:
        """A reconnect keeps the immediate attempts: no hold signal.

        Drives the lifecycle directly: a lifecycle that already connected
        once treats a SESSION_BUSY_REJECT (the transient the auth-stage
        0xFFFFFFFF mapping produces) as an ordinary immediate retry, so
        the start-up hold event stays clear through every attempt.
        """

        mech = FakeMechanism()
        lifecycle = CoreRadioSessionLifecycle(
            mech,
            reject_cooldown_s=0.0,
            not_ready_cooldown_s=0.0,
            max_connect_attempts=3,
            first_connect_busy_cooldown_s=30.0,
            first_connect_busy_budget_s=600.0,
        )
        lifecycle._connected_once = True
        for _ in range(3):
            mech.queue_busy_reject()
        # Sampled at each attempt's start: the terminal DISCONNECTED
        # clears the event, so only per-attempt samples prove the
        # reconnect path never raised it mid-loop.
        seen: list[bool] = []
        original_attempt = mech.connect_attempt

        async def _probing_attempt() -> AttemptResult:
            seen.append(lifecycle.startup_busy_hold_event.is_set())
            return await original_attempt()

        mech.connect_attempt = _probing_attempt  # type: ignore[method-assign]
        with pytest.raises(ConnectionError, match="never became ready"):
            await lifecycle.connect()

        assert mech.connect_calls == 3
        assert mech.release_calls == 3
        assert seen == [False, False, False]
        assert lifecycle.startup_busy_hold_event.is_set() is False
        assert lifecycle.startup_busy_hold_retry_in_seconds is None

    @pytest.mark.asyncio
    async def test_not_ready_between_busy_passes_keeps_the_hold_retry_number(
        self,
    ) -> None:
        """A NOT_READY pass mid-hold keeps serving the hold's retry number.

        Pin for the served wire: after a busy pass, a SESSION_NOT_READY
        attempt must not clear the hold's cooldown total while the hold
        event is still set — ``startup_busy_hold_retry_in_seconds`` keeps
        reporting the hold's cooldown (never null, which ``/readyz``
        would serve as 0.0) until the grant lands.
        """

        mech = FakeMechanism()
        busy_cooldown = 0.05
        lifecycle = CoreRadioSessionLifecycle(
            mech,
            reject_cooldown_s=30.0,
            not_ready_cooldown_s=0.07,
            max_connect_attempts=5,
            first_connect_busy_cooldown_s=busy_cooldown,
            first_connect_busy_budget_s=600.0,
        )
        mech.queue_busy_reject()
        mech.queue_not_ready()
        mech.queue_busy_reject()
        mech.queue_ok()
        samples: list[float | None] = []
        original_attempt = mech.connect_attempt

        async def _probing_attempt() -> AttemptResult:
            samples.append(lifecycle.startup_busy_hold_retry_in_seconds)
            return await original_attempt()

        mech.connect_attempt = _probing_attempt  # type: ignore[method-assign]
        await lifecycle.connect()

        assert samples == [None, busy_cooldown, busy_cooldown, busy_cooldown]
        assert lifecycle.startup_busy_hold_event.is_set() is False
        assert lifecycle.startup_busy_hold_retry_in_seconds is None

    @pytest.mark.asyncio
    async def test_busy_retry_number_inside_cooldown_is_the_live_remainder(
        self,
    ) -> None:
        """Inside the busy COOLDOWN sleep the number is the live remainder.

        Sampled from within the cooldown wait itself: it equals the
        lifecycle's remaining time and is already less than the hold's
        total — the value ``/readyz`` serves as ``retryInSeconds`` while
        a re-login waits.
        """

        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        clock = _VirtualMonotonic()
        tokens = [0x6000 + i for i in range(3)]
        logins = [_build_login_response(token=t) for t in tokens]
        busy_left = [1]
        samples: list[tuple[float | None, float | None, float | None]] = []

        async def _status_flow() -> int:
            if busy_left[0] > 0:
                busy_left[0] -= 1
                clock.advance(_REJECT_STEP_S)
                radio._last_status_error = 0xFFFFFFFF
                return 0
            radio._last_status_error = 0
            return 50001

        async def _sampling_sleep(delay: float) -> None:
            lifecycle = radio._session_lifecycle
            if lifecycle.status.cooldown_remaining_s is None:
                # Not a cooldown wait (the connect's own pacing sleeps):
                # pass through as a no-op like the stack's sleep double.
                return
            # Half the cooldown elapses "during" the wait, so the live
            # remainder is strictly below the hold's total when sampled.
            clock.advance(delay / 2)
            samples.append(
                (
                    radio.startup_busy_hold_retry_in_seconds,
                    lifecycle.status.cooldown_remaining_s,
                    lifecycle.status.cooldown_total_s,
                )
            )

        send_mock = AsyncMock()
        with _busy_hold_patch_stack(
            radio,
            clock,
            login_responses=logins,
            status_flow=_status_flow,
            send_mock=send_mock,
        ):
            with patch(
                "rigplane.runtime.session_lifecycle.asyncio.sleep",
                new=_sampling_sleep,
            ):
                await radio.connect()

        assert radio._civ_port == 50001
        assert len(samples) == 1
        retry, remaining, total = samples[0]
        assert isinstance(retry, float)
        assert isinstance(remaining, float)
        assert isinstance(total, float)
        assert retry == remaining
        assert retry < total
        assert radio.startup_busy_hold_event.is_set() is False
        assert radio.startup_busy_hold_retry_in_seconds is None

    @pytest.mark.asyncio
    async def test_discovery_pass_reports_the_busy_hold_like_a_busy_reject(
        self,
    ) -> None:
        """A discovery-timeout pass holds the same busy signal as a reject.

        MOR-3391 part 2 round 3: after one busy reject an unanswered
        discovery counts as a busy pass, so the hold event stays set and
        the retry number keeps reporting the cooldown until the grant
        clears both. The live-holder stop is off here (``None``) to reach
        the grant; the stop's own clearing is pinned below.
        """

        mech = FakeMechanism()
        busy_cooldown = 0.05
        lifecycle = CoreRadioSessionLifecycle(
            mech,
            reject_cooldown_s=30.0,
            not_ready_cooldown_s=0.07,
            max_connect_attempts=5,
            first_connect_busy_cooldown_s=busy_cooldown,
            first_connect_busy_budget_s=600.0,
            first_connect_busy_live_holder_after_s=None,
        )
        mech.queue_busy_reject()
        mech.queue_discovery_timeout()
        mech.queue_ok()
        samples: list[tuple[bool, float | None]] = []
        original_attempt = mech.connect_attempt

        async def _probing_attempt() -> AttemptResult:
            samples.append(
                (
                    lifecycle.startup_busy_hold_event.is_set(),
                    lifecycle.startup_busy_hold_retry_in_seconds,
                )
            )
            return await original_attempt()

        mech.connect_attempt = _probing_attempt  # type: ignore[method-assign]
        await lifecycle.connect()

        assert samples == [
            (False, None),
            (True, busy_cooldown),
            (True, busy_cooldown),
        ]
        assert lifecycle.startup_busy_hold_event.is_set() is False
        assert lifecycle.startup_busy_hold_retry_in_seconds is None

    @pytest.mark.asyncio
    async def test_live_holder_stop_after_a_discovery_pass_clears_the_signal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stop clears the hold even when a discovery pass triggers it.

        MOR-3391 part 2 round 3: the first attempt busy-rejects (one real
        cooldown, so the second attempt starts past the compressed
        threshold) and the second — a discovery timeout — already proves
        a live holder, so the loop raises the honest busy error (never
        the discovery message) and the hold clears with the stop. The
        production 90 s cadence is pinned by part 1b's own rows.
        """

        mech = FakeMechanism()
        lifecycle = CoreRadioSessionLifecycle(
            mech,
            reject_cooldown_s=30.0,
            not_ready_cooldown_s=0.07,
            max_connect_attempts=5,
            first_connect_busy_cooldown_s=0.05,
            first_connect_busy_budget_s=600.0,
            first_connect_busy_live_holder_after_s=0.01,
        )
        mech.queue_busy_reject()
        mech.queue_discovery_timeout()
        samples: list[tuple[bool, float | None]] = []
        real_sleep = asyncio.sleep

        async def _recording_sleep(delay: float) -> None:
            samples.append(
                (
                    lifecycle.startup_busy_hold_event.is_set(),
                    lifecycle.startup_busy_hold_retry_in_seconds,
                )
            )
            await real_sleep(delay)

        monkeypatch.setattr(
            "rigplane.runtime.session_lifecycle.asyncio.sleep", _recording_sleep
        )

        with pytest.raises(ConnectionError) as excinfo:
            await lifecycle.connect()

        message = str(excinfo.value)
        assert "another client holds the session" in message
        assert "did not respond to discovery" not in message
        assert mech.connect_calls == 2
        # The single pass set the hold before the stop cleared it.
        assert len(samples) == 1
        assert samples[0][0] is True
        assert isinstance(samples[0][1], float)
        assert lifecycle.state is LifecycleState.DISCONNECTED
        assert lifecycle.startup_busy_hold_event.is_set() is False
        assert lifecycle.startup_busy_hold_retry_in_seconds is None


class _FakeSocket:
    def __init__(self, sockname: tuple[str, int]) -> None:
        self.sockname = sockname
        self.bound: tuple[str, int] | None = None
        self.connected: tuple[str, int] | None = None
        self.close_count = 0

    def connect(self, addr: tuple[str, int]) -> None:
        self.connected = addr

    def bind(self, addr: tuple[str, int]) -> None:
        self.bound = addr

    def getsockname(self) -> tuple[str, int]:
        return self.sockname

    def close(self) -> None:
        self.close_count += 1


class TestWifiBindBehavior:
    @pytest.mark.asyncio
    async def test_connect_uses_routed_local_bind_host_for_control_and_civ(
        self,
    ) -> None:
        radio = IcomRadio("192.168.2.1", username="u", password="p", model="IC-7610")
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
            # Same MOR-3064 seam as the discovery-retry test: the fake CI-V
            # transport runs no rx pump here either.
            patch.object(
                radio,
                "_read_lan_identity_payload",
                new=AsyncMock(return_value=b"\x98"),
            ),
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
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
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
