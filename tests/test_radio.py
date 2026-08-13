"""Tests for IcomRadio high-level API."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from rigplane import IC_7610_ADDR
from rigplane.backends.icom7610.drivers import serial_session as serial
from rigplane.commands import (
    _CMD_ACK,
    _CMD_FREQ_GET,
    _CMD_LEVEL,
    _CMD_METER,
    _CMD_PTT,
    _SUB_ALC_METER,
    _SUB_POWER_METER,
    _SUB_PTT,
    _SUB_RF_POWER,
    _SUB_S_METER,
    _SUB_SWR_METER,
    CONTROLLER_ADDR,
    build_civ_frame,
    build_cmd29_frame,
)
from rigplane.commander import Priority
from rigplane.exceptions import CommandError, ConnectionError, TimeoutError
from rigplane.core import tx_safety as tx
from rigplane.radio import IcomRadio
from rigplane.types import (
    AgcMode,
    AudioCodec,
    AudioPeakFilter,
    BreakInMode,
    CivFrame,
    FilterShape,
    Mode,
    SsbTxBandwidth,
    bcd_encode,
)

from _helpers import ack_response as _ack_response
from _helpers import freq_response as _freq_response
from _helpers import mode_response as _mode_response
from _helpers import wrap_civ_in_udp as _wrap_civ_in_udp

# ---------------------------------------------------------------------------
# Helpers — build fake radio responses as UDP packets wrapping CI-V frames
# ---------------------------------------------------------------------------


def _meter_response(sub: int, value: int) -> bytes:
    """Build a CI-V meter response wrapped in UDP."""
    # BCD encode the value (0-255 as 4-digit BCD in 2 bytes)
    d = f"{value:04d}"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, _CMD_METER, sub=sub, data=bytes([b0, b1])
    )
    return _wrap_civ_in_udp(civ)


def _nak_response() -> bytes:
    """Build a CI-V NAK wrapped in UDP."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFA)
    return _wrap_civ_in_udp(civ)


def _power_response(level: int) -> bytes:
    """Build a CI-V power level response wrapped in UDP."""
    d = f"{level:04d}"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_LEVEL,
        sub=_SUB_RF_POWER,
        data=bytes([b0, b1]),
    )
    return _wrap_civ_in_udp(civ)


def _ptt_response(on: bool) -> bytes:
    """Build a CI-V PTT status response wrapped in UDP."""
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_PTT,
        sub=_SUB_PTT,
        data=bytes([0x01 if on else 0x00]),
    )
    return _wrap_civ_in_udp(civ)


async def _route_ptt(
    radio: IcomRadio,
    data: bytes,
    *,
    to_addr: int = CONTROLLER_ADDR,
    from_addr: int = IC_7610_ADDR,
) -> None:
    await radio._civ_runtime._route_civ_frame(
        CivFrame(to_addr, from_addr, 0x1C, sub=0x00, data=data),
        generation=radio._civ_epoch,
    )


def _bcd_bytes(value: int, digits: int = 4) -> bytes:
    """Build packed BCD bytes for small CI-V register payloads."""
    text = f"{value:0{digits}d}"
    if len(text) % 2 != 0:
        text = f"0{text}"
    return bytes(
        (int(text[index]) << 4) | int(text[index + 1])
        for index in range(0, len(text), 2)
    )


def _level_response(sub: int, value: int, *, receiver: int | None = None) -> bytes:
    """Build a CI-V level response, optionally cmd29-wrapped."""
    if receiver is None:
        civ = build_civ_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            _CMD_LEVEL,
            sub=sub,
            data=_bcd_bytes(value),
        )
    else:
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            _CMD_LEVEL,
            sub=sub,
            data=_bcd_bytes(value),
            receiver=receiver,
        )
    return _wrap_civ_in_udp(civ)


def _ctl_mem_response(
    sub: int,
    payload: bytes,
    *,
    prefix: bytes = b"",
    receiver: int | None = None,
) -> bytes:
    """Build a CI-V 0x1A response, optionally cmd29-wrapped."""
    if receiver is None:
        civ = build_civ_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x1A,
            sub=sub,
            data=prefix + payload,
        )
    else:
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x1A,
            sub=sub,
            data=prefix + payload,
            receiver=receiver,
        )
    return _wrap_civ_in_udp(civ)


def _function_response(
    sub: int,
    payload: bytes,
    *,
    receiver: int | None = None,
) -> bytes:
    """Build a CI-V 0x16 response, optionally cmd29-wrapped."""
    if receiver is None:
        civ = build_civ_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x16,
            sub=sub,
            data=payload,
        )
    else:
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x16,
            sub=sub,
            data=payload,
            receiver=receiver,
        )
    return _wrap_civ_in_udp(civ)


def _meter_status_response(
    sub: int,
    value: int,
    *,
    receiver: int | None = None,
) -> bytes:
    """Build a CI-V 0x15 status response, optionally cmd29-wrapped."""
    if receiver is None:
        civ = build_civ_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x15,
            sub=sub,
            data=bytes([value]),
        )
    else:
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x15,
            sub=sub,
            data=bytes([value]),
            receiver=receiver,
        )
    return _wrap_civ_in_udp(civ)


def _scope_response(sub: int, payload: bytes) -> bytes:
    """Build a CI-V scope-control response wrapped in UDP."""
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x27,
        sub=sub,
        data=payload,
    )
    return _wrap_civ_in_udp(civ)


# ---------------------------------------------------------------------------
# MockTransport — replaces IcomTransport for unit testing
# ---------------------------------------------------------------------------


class MockTransport:
    """Mock transport that queues responses for receive_packet."""

    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.sent_packets: list[bytes] = []
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._responses_by_send: dict[int, list[bytes]] = {}
        self._packet_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.my_id: int = 0x00010001
        self.remote_id: int = 0xDEADBEEF
        self.send_seq: int = 0
        self.ping_seq: int = 0
        self.rx_packet_count: int = 0

    async def connect(self, host: str, port: int) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass

    async def send_tracked(self, data: bytes) -> None:
        self.sent_packets.append(data)
        self.send_seq += 1
        for pkt in self._responses_by_send.pop(self.send_seq, []):
            self._responses.put_nowait(pkt)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        try:
            return await asyncio.wait_for(self._responses.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError()

    def queue_response(self, data: bytes) -> None:
        self._responses.put_nowait(data)

    def queue_response_on_send(self, send_number: int, data: bytes) -> None:
        """Queue a response to be released after N-th send_tracked() call.

        Useful for tests where one high-level API call sends multiple CI-V commands
        and each command should receive its own response in-order.
        """
        self._responses_by_send.setdefault(send_number, []).append(data)

    @property
    def _raw_send(self):
        return lambda data: self.sent_packets.append(data)

    @_raw_send.setter
    def _raw_send(self, value):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(mock_transport: MockTransport):
    """Shared radio fixture with explicit teardown to silence the
    ``Radio collected with active connection/tasks`` __del__ warning
    from tests that bypass the real connect/disconnect lifecycle."""
    r = IcomRadio("192.168.1.100", timeout=0.05)
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    yield r
    r._connected = False  # reset _conn_state so __del__ stays quiet


class TestContextManager:
    """Test connect/disconnect lifecycle with mocked transport."""

    @pytest.mark.asyncio
    async def test_disconnect(self, mock_transport: MockTransport) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        await radio.disconnect()
        assert not radio.connected
        assert mock_transport.disconnected

    @pytest.mark.asyncio
    async def test_context_manager_exit(self, mock_transport: MockTransport) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        # __aenter__ calls connect() which redoes handshake;
        # test __aexit__ path directly instead
        assert radio.connected
        await radio.__aexit__(None, None, None)
        assert not radio.connected


class TestFrequency:
    """Test frequency get/set."""

    @pytest.mark.asyncio
    async def test_get_frequency(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_freq_response(14_074_000))
        freq = await radio.get_freq()
        assert freq == 14_074_000

    @pytest.mark.asyncio
    async def test_set_frequency(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_freq(7_074_000)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_frequency_no_response_needed(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_frequency is fire-and-forget — completes without any radio response."""
        await radio.set_freq(14_074_000)
        assert radio._last_freq_hz == 14_074_000


class TestMode:
    """Test mode get/set."""

    @pytest.mark.asyncio
    async def test_get_mode(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_mode_response(Mode.USB))
        mode_name, _filt = await radio.get_mode()
        assert mode_name == "USB"

    @pytest.mark.asyncio
    async def test_set_mode(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_mode(Mode.LSB)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_mode_no_response_needed(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_mode is fire-and-forget — completes without any radio response."""
        await radio.set_mode(Mode.USB)
        assert radio._last_mode == Mode.USB

    @pytest.mark.asyncio
    async def test_set_mode_from_string(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_mode("USB")
        assert len(mock_transport.sent_packets) > 0


class TestSetModeSelected0x26:
    """MAIN set_mode routes through CI-V 0x26 0x00 for rigs declaring
    ``set_selected_mode`` (X6200), and stays on the bare 0x06 for rigs that
    do not (IC-7610).

    Note:
        mock_server does not handle 0x26; the emitted bytes are asserted via
        ``sent_packets[-1]``. Live IC-7610 + X6200 verification is done
        separately by the orchestrator.
    """

    def _make_radio(self, model: str, mock_transport: MockTransport) -> IcomRadio:
        r = IcomRadio("192.168.1.100", model=model, timeout=0.05)
        r._civ_transport = mock_transport
        r._ctrl_transport = mock_transport
        r._connected = True
        return r

    @pytest.mark.asyncio
    async def test_x6200_routes_via_0x26(self, mock_transport: MockTransport) -> None:
        # X6200: civ_addr 0xA4, declares set_selected_mode → 0x26 path.
        # Frame tail: 26 00 <receiver=00> <mode=LSB=00> <data_mode=00>
        #             <filter=default 1> FD.
        radio = self._make_radio("X6200", mock_transport)
        try:
            assert radio._profile.set_mode_via_selected is True
            await radio.set_mode(Mode.LSB)
            assert mock_transport.sent_packets[-1].endswith(
                b"\xfe\xfe\xa4\xe0\x26\x00\x00\x00\x01\xfd"
            )
        finally:
            radio._connected = False

    @pytest.mark.asyncio
    async def test_ic7610_stays_on_0x06_regression_lock(
        self, mock_transport: MockTransport
    ) -> None:
        # IC-7610 must NOT declare set_selected_mode → unchanged 0x06 path.
        # This locks zero flagship regression (MOR-184): the flagship uses
        # the plain 0x06 mode set, never the 0x26 selected-mode form.
        #
        # MOR-495: the lock is INTENTIONALLY relaxed to permit an optional
        # filter byte (the 2-byte 0x06 form), because the web mode-change now
        # recalls the destination mode's remembered filter to mirror the front
        # panel.  The hard invariant — "0x06, NOT 0x26 selected-mode" — is
        # preserved in both the mode-only and mode+filter cases below.
        radio = self._make_radio("IC-7610", mock_transport)
        try:
            assert radio._profile.set_mode_via_selected is False
            # Mode-only change → 1-byte 0x06 (radio applies its own default).
            await radio.set_mode(Mode.LSB)
            sent = mock_transport.sent_packets[-1]
            assert sent.endswith(b"\xfe\xfe\x98\xe0\x06\x00\xfd")
            assert b"\x26\x00" not in sent
        finally:
            radio._connected = False

    @pytest.mark.asyncio
    async def test_ic7610_mode_with_filter_emits_two_byte_0x06(
        self, mock_transport: MockTransport
    ) -> None:
        # MOR-495: a mode change carrying a remembered filter must reach the
        # wire as the 2-byte 0x06 form (mode byte + filter byte), so the radio
        # lands on the requested filter rather than the mode-default filter.
        # USB=0x01, FIL1=0x01 → ``... 06 01 01 FD``.  Still 0x06, never 0x26.
        radio = self._make_radio("IC-7610", mock_transport)
        try:
            assert radio._profile.set_mode_via_selected is False
            await radio.set_mode(Mode.USB, filter_width=1)
            sent = mock_transport.sent_packets[-1]
            assert sent.endswith(b"\xfe\xfe\x98\xe0\x06\x01\x01\xfd")
            assert b"\x26\x00" not in sent
        finally:
            radio._connected = False

    @pytest.mark.asyncio
    async def test_x6200_fills_data_mode_and_filter(
        self, mock_transport: MockTransport
    ) -> None:
        # A mode-only change preserves the radio's current data_mode/filter:
        # data_mode 1 (from MAIN state), filter 2 (from _filter_width cache).
        # Frame tail: 26 <receiver=00> <mode=USB=01> <data_mode=01>
        #             <filter=02> FD.
        radio = self._make_radio("X6200", mock_transport)
        try:
            radio._radio_state.main.data_mode = 1
            radio._filter_width = 2
            await radio.set_mode(Mode.USB)
            assert mock_transport.sent_packets[-1].endswith(b"\x26\x00\x01\x01\x02\xfd")
        finally:
            radio._connected = False


class TestMeters:
    """Test meter readings."""

    @pytest.mark.asyncio
    async def test_get_s_meter(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_meter_response(_SUB_S_METER, 120))
        val = await radio.get_s_meter()
        assert val == 120

    @pytest.mark.asyncio
    async def test_get_swr(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # IC-7610 profile interpolates raw=50 between (48, 1.5) and
        # (80, 2.0): 1.5 + (50-48)/(80-48) * (2.0-1.5) = 1.53125.
        mock_transport.queue_response(_meter_response(_SUB_SWR_METER, 50))
        val = await radio.get_swr()
        assert val == pytest.approx(1.53125)

    @pytest.mark.asyncio
    async def test_get_swr_meter_raw(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_meter_response(_SUB_SWR_METER, 50))
        val = await radio.get_swr_meter()
        assert val == 50

    @pytest.mark.asyncio
    async def test_get_alc_meter(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_meter_response(_SUB_ALC_METER, 80))
        val = await radio.get_alc_meter()
        assert val == 80

    @pytest.mark.asyncio
    async def test_get_swr_meter(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_meter_response(_SUB_SWR_METER, 50))
        val = await radio.get_swr_meter()
        assert val == 50

    @pytest.mark.asyncio
    async def test_get_power_meter(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_meter_response(_SUB_POWER_METER, 200))
        val = await radio.get_power_meter()
        assert val == 200

    def test_meters_capable_protocol_satisfied(self, radio: IcomRadio) -> None:
        """IcomRadio satisfies the extended MetersCapable protocol (#1104)."""
        from rigplane.radio_protocol import MetersCapable

        assert isinstance(radio, MetersCapable)
        # Spot-check the new methods exist with the expected names.
        for name in ("get_power_meter", "get_alc_meter", "get_swr_meter"):
            assert callable(getattr(radio, name)), f"{name} missing"


class TestPower:
    """Test power get/set."""

    @pytest.mark.asyncio
    async def test_get_power(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_power_response(128))
        val = await radio.get_rf_power()
        assert val == 128

    @pytest.mark.asyncio
    async def test_set_power(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_rf_power(200)
        assert len(mock_transport.sent_packets) > 0


class TestRfGainAfLevel:
    """Test RF Gain and AF Level get/set."""

    @pytest.mark.asyncio
    async def test_set_rf_gain(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_rf_gain(200)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_rf_gain_zero(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_rf_gain(0)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_rf_gain_out_of_range(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError):
            await radio.set_rf_gain(256)
        with pytest.raises(ValueError):
            await radio.set_rf_gain(-1)

    @pytest.mark.asyncio
    async def test_set_af_level(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_af_level(128)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_af_level_zero(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_af_level(0)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_af_level_out_of_range(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError):
            await radio.set_af_level(256)
        with pytest.raises(ValueError):
            await radio.set_af_level(-1)

    @pytest.mark.asyncio
    async def test_get_rf_gain_sub_uses_cmd29(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """get_rf_gain(receiver=1) should send a cmd29-wrapped frame."""
        d = f"{200:04d}"
        b0 = (int(d[0]) << 4) | int(d[1])
        b1 = (int(d[2]) << 4) | int(d[3])
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            _CMD_LEVEL,
            sub=0x02,
            data=bytes([b0, b1]),
            receiver=1,
        )
        mock_transport.queue_response(_wrap_civ_in_udp(civ))
        result = await radio.get_rf_gain(receiver=1)
        # Verify the request used cmd29 routing
        sent = bytes(mock_transport.sent_packets[-1])
        # Find CI-V payload — last sent frame must be cmd29 (0x29) wrapping 0x14 0x02
        assert b"\x29\x01\x14\x02" in sent
        assert result == 200

    @pytest.mark.asyncio
    async def test_get_af_level_sub_uses_cmd29(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """get_af_level(receiver=1) should send a cmd29-wrapped frame."""
        d = f"{150:04d}"
        b0 = (int(d[0]) << 4) | int(d[1])
        b1 = (int(d[2]) << 4) | int(d[3])
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            _CMD_LEVEL,
            sub=0x01,
            data=bytes([b0, b1]),
            receiver=1,
        )
        mock_transport.queue_response(_wrap_civ_in_udp(civ))
        result = await radio.get_af_level(receiver=1)
        sent = bytes(mock_transport.sent_packets[-1])
        assert b"\x29\x01\x14\x01" in sent
        assert result == 150


class TestLevelsCapableProtocol:
    """Protocol satisfaction: backends must implement LevelsCapable getters/setters."""

    def test_icom_radio_satisfies_levels_capable(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        from rigplane.radio_protocol import LevelsCapable

        assert isinstance(radio, LevelsCapable)

    def test_icom_radio_has_af_rf_getters_with_receiver_param(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        import inspect

        # Both getters must accept a `receiver` keyword (P3-03 fix).
        for fn_name in ("get_af_level", "get_rf_gain"):
            sig = inspect.signature(getattr(radio, fn_name))
            assert "receiver" in sig.parameters, (
                f"{fn_name} is missing `receiver` parameter"
            )


class TestSquelch:
    """Test get_squelch — verifies cmd29 path and frame parsing (issue #1093)."""

    @pytest.mark.asyncio
    async def test_levels_get_squelch_icom(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """get_squelch on MAIN parses a non-cmd29 0x14 0x03 BCD response."""
        mock_transport.queue_response(_level_response(0x03, 200))
        assert await radio.get_squelch() == 200

    @pytest.mark.asyncio
    async def test_levels_get_squelch_icom_sub(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """get_squelch on SUB parses a cmd29-wrapped 0x14 0x03 BCD response."""
        mock_transport.queue_response(_level_response(0x03, 128, receiver=1))
        assert await radio.get_squelch(receiver=1) == 128


class TestPtt:
    """Test PTT toggle."""

    @pytest.mark.timeout(2)
    @pytest.mark.parametrize("invalidation", ["rebind", "poison", "reconnect"])
    async def test_managed_ptt_port_token_safety(
        self, radio: IcomRadio, invalidation: str
    ) -> None:
        sent: list[bytes] = []
        responses: asyncio.Queue[bytes] = asyncio.Queue()
        send_release, disconnect_release = asyncio.Event(), asyncio.Event()

        async def send(frame: bytes) -> None:
            await send_release.wait()
            sent.append(frame)

        async def receive(**_kwargs: float) -> bytes:
            return await responses.get()

        link = AsyncMock()
        link.send.side_effect = send
        link.receive.side_effect = receive
        link.disconnect.side_effect = OSError("pre-disconnect failure")
        radio._civ_transport = serial.SerialCivTransport(link)
        observations: list[tx.ProviderPttObservation] = []
        observer = observations.append
        assert radio._capture_managed_tx_port(11, observer)
        runtime = radio._civ_runtime
        token = runtime._managed_tx_ports[11]
        if invalidation == "reconnect":
            replacement = MockTransport()
            checks = 0
            paced, release = asyncio.Event(), asyncio.Event()
            pending: asyncio.Task[None] | None = None
            current = runtime._managed_tx_port_is_current

            def reconnect_after_validation(candidate: object) -> bool:
                nonlocal checks
                checks += 1
                valid = current(candidate)  # type: ignore[arg-type]
                if checks == 2:
                    radio._civ_transport = replacement
                return valid

            async def blocked_pacing(_delay: float) -> None:
                paced.set()
                await release.wait()

            send_release.set()
            link.disconnect.side_effect = None
            try:
                with patch.object(
                    runtime,
                    "_managed_tx_port_is_current",
                    reconnect_after_validation,
                ):
                    await asyncio.wait_for(radio._write_managed_ptt(11, True), 0.5)
                if radio._civ_transport is not replacement:
                    radio._civ_transport = replacement
                await asyncio.wait_for(radio._retire_managed_tx_port(11), 0.5)
                assert link.send.await_count == 1
                assert replacement.sent_packets == []
                assert not replacement.disconnected
                assert radio._civ_transport is replacement
                await runtime.stop_pump()

                assert radio._capture_managed_tx_port(12, observer)
                paced_token = runtime._managed_tx_ports[12]
                radio._last_civ_send_monotonic = asyncio.get_running_loop().time()
                radio._civ_min_interval = 1.0
                frame = build_civ_frame(
                    IC_7610_ADDR,
                    CONTROLLER_ADDR,
                    _CMD_PTT,
                    sub=_SUB_PTT,
                    data=b"\x01",
                )
                with patch("rigplane.runtime._civ_rx.asyncio.sleep", blocked_pacing):
                    pending = asyncio.create_task(
                        runtime._send_civ_frame_now(frame, owner=paced_token)
                    )
                    await asyncio.wait_for(paced.wait(), 0.2)
                    runtime._poison_managed_tx_port(paced_token)
                    release.set()
                    with pytest.raises(ConnectionError, match="before dispatch"):
                        await asyncio.wait_for(pending, 0.5)
                assert replacement.sent_packets == []
            finally:
                release.set()
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                if 12 in runtime._managed_tx_ports:
                    await asyncio.wait_for(radio._retire_managed_tx_port(12), 0.5)
                await runtime.stop_pump()
            return

        read = radio._request_authoritative_ptt_read
        send_release.set()
        pending_read = asyncio.create_task(read(11, observer))
        while not sent:
            await asyncio.sleep(0)
        send_release.clear()
        pending_write = asyncio.create_task(radio._write_managed_ptt(11, True))
        while link.send.await_count < 2:
            await asyncio.sleep(0)
        if invalidation == "poison":
            assert runtime._managed_tx_port_is_current(token)
            runtime._poison_managed_tx_port(token)
            assert runtime._managed_tx_ports[11] is token
        else:
            runtime.bind_ptt_observer(provider_generation=12, observer=lambda _: None)
        send_release.set()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(pending_write, 0.5)
        assert len(sent) == 1
        responses.put_nowait(serial._unwrap_civ_frame(_ptt_response(True)))
        with pytest.raises(CommandError, match="authoritative"):
            await asyncio.wait_for(pending_read, 0.5)
        assert not observations
        replacement = MockTransport()
        radio._civ_transport = replacement
        with pytest.raises(OSError, match="pre-disconnect"):
            await asyncio.wait_for(radio._retire_managed_tx_port(11), 0.5)
        link.disconnect.side_effect = disconnect_release.wait
        retry = asyncio.create_task(radio._retire_managed_tx_port(11))
        while link.disconnect.await_count < 2:
            await asyncio.sleep(0)
        duplicate = asyncio.create_task(radio._retire_managed_tx_port(11))
        await asyncio.sleep(0)
        retry.cancel()
        with pytest.raises(asyncio.CancelledError):
            await retry
        assert not duplicate.done() and link.disconnect.await_count == 2
        disconnect_release.set()
        await asyncio.wait_for(duplicate, 0.5)
        assert not replacement.disconnected and radio._civ_transport is replacement
        with pytest.raises(ConnectionError):
            radio._capture_managed_tx_port(11, observer)
        await radio._civ_runtime.stop_pump()

    @pytest.mark.asyncio
    async def test_set_ptt_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_ptt is fire-and-forget — no ACK response needed."""
        await radio.set_ptt(True)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_ptt_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_ptt is fire-and-forget — no ACK response needed."""
        await radio.set_ptt(False)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_ptt_uses_immediate_priority(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Regression (MOR-497i): set_ptt must stay at Priority.IMMEDIATE so
        the BACKGROUND poll change never demotes TX keying."""
        with patch.object(radio, "_send_civ_raw", autospec=True) as mock_raw:
            await radio.set_ptt(True)
        assert mock_raw.call_count == 1
        assert mock_raw.call_args.kwargs["priority"] == Priority.IMMEDIATE

    @pytest.mark.asyncio
    async def test_set_ptt_does_not_update_state_cache_optimistically(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """A local write is not authoritative PTT state."""
        await radio.set_ptt(True)
        assert radio.state_cache.ptt is False
        assert radio.state_cache.ptt_ts == 0.0

        await radio.set_ptt(False)
        assert radio.state_cache.ptt is False
        assert radio.state_cache.ptt_ts == 0.0

    @pytest.mark.asyncio
    async def test_ptt_authority_requires_exact_radio_response(
        self, radio: IcomRadio
    ) -> None:
        observations: list[tx.ProviderPttObservation] = []
        radio._bind_authoritative_ptt_observer(
            provider_generation=7, observer=observations.append
        )

        for data in (b"", b"\x02", b"\x01\x00"):
            await _route_ptt(radio, data)
        for to_addr, from_addr in (
            (CONTROLLER_ADDR, 0x99),
            (0x99, IC_7610_ADDR),
        ):
            await _route_ptt(radio, b"\x01", to_addr=to_addr, from_addr=from_addr)
        await radio._civ_runtime._route_civ_frame(
            CivFrame(
                to_addr=CONTROLLER_ADDR,
                from_addr=IC_7610_ADDR,
                command=0xFB,
            ),
            generation=radio._civ_epoch,
        )
        assert observations == []

        for value in (b"\x01", b"\x01", b"\x00"):
            await _route_ptt(radio, value)
        assert [item.value for item in observations] == [
            tx.RadioTx.ON,
            tx.RadioTx.ON,
            tx.RadioTx.OFF,
        ]
        assert [item.ptt_observation_seq for item in observations] == [1, 2, 3]
        assert {item.provider_generation for item in observations} == {7}

        replaced: list[tx.ProviderPttObservation] = []
        radio._bind_authoritative_ptt_observer(
            provider_generation=7, observer=replaced.append
        )
        await _route_ptt(radio, b"\x00")
        assert replaced[0].ptt_observation_seq == 4

        rebound: list[tx.ProviderPttObservation] = []
        radio._bind_authoritative_ptt_observer(
            provider_generation=9, observer=rebound.append
        )
        await _route_ptt(radio, b"\x00")
        assert rebound[0].provider_generation == 9
        assert rebound[0].ptt_observation_seq == 1

    @pytest.mark.asyncio
    async def test_same_generation_rebind_off_crosses_supervisor_boundary(
        self, radio: IcomRadio
    ) -> None:
        supervisor = tx.TxSafetySupervisor()
        supervisor.replace_provider(7, ready=True)
        radio._bind_authoritative_ptt_observer(
            provider_generation=7, observer=supervisor.observe_ptt
        )
        await _route_ptt(radio, b"\x00")
        owner = tx.TxOwner(tx.TxSource.INTERNAL, "same-generation-rebind")
        acquired = supervisor.request_on(owner)
        await _route_ptt(radio, b"\x01")
        assert acquired.snapshot.lease_id is not None
        supervisor.request_off(owner, acquired.snapshot.lease_id)
        assert supervisor.snapshot.phase is tx.TxPhase.RELEASE_REQUIRED

        radio._unbind_authoritative_ptt_observer()
        radio._bind_authoritative_ptt_observer(
            provider_generation=7, observer=supervisor.observe_ptt
        )
        await _route_ptt(radio, b"\x00")
        assert supervisor.snapshot.phase is tx.TxPhase.IDLE

    @pytest.mark.asyncio
    async def test_ptt_authority_rejects_old_rx_pump_generation(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        observations: list[tx.ProviderPttObservation] = []
        radio._civ_runtime.start_pump()
        radio._civ_runtime.advance_generation("unit-test-reconnect")
        radio._bind_authoritative_ptt_observer(
            provider_generation=8, observer=observations.append
        )
        mock_transport.queue_response(_ptt_response(False))
        await asyncio.sleep(0.01)
        assert observations == []

        await radio._civ_runtime.stop_pump()
        radio._civ_runtime.start_pump()
        mock_transport.queue_response(_ptt_response(False))
        await asyncio.sleep(0.01)
        try:
            assert len(observations) == 1
            assert observations[0].value is tx.RadioTx.OFF
            assert observations[0].provider_generation == 8
        finally:
            await radio._civ_runtime.stop_pump()

    async def test_fresh_ptt_read_rejects_rebind_and_equal_distinct_observer(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        observations: list[tx.ProviderPttObservation] = []
        observer = observations.append
        read = radio._request_authoritative_ptt_read
        radio._bind_authoritative_ptt_observer(provider_generation=8, observer=observer)
        await _route_ptt(radio, b"\x01")
        pending = asyncio.create_task(read(provider_generation=8, observer=observer))
        while not mock_transport.sent_packets:
            await asyncio.sleep(0)
        radio._unbind_authoritative_ptt_observer()
        radio._bind_authoritative_ptt_observer(provider_generation=8, observer=observer)
        mock_transport.queue_response(_ptt_response(False))
        with pytest.raises(CommandError, match="authoritative"):
            await pending
        await _route_ptt(radio, b"\x00")
        assert [item.ptt_observation_seq for item in observations] == [1, 2]

        class EqualObserver:
            def __init__(self) -> None:
                self.calls: list[tx.ProviderPttObservation] = []

            def __call__(self, observation: tx.ProviderPttObservation) -> None:
                self.calls.append(observation)

            def __eq__(self, _other: object) -> bool:
                return True

        bound, collision = EqualObserver(), EqualObserver()
        radio._bind_authoritative_ptt_observer(provider_generation=8, observer=bound)
        with pytest.raises(CommandError, match="authoritative"):
            await read(provider_generation=8, observer=collision)
        assert not bound.calls
        await radio._civ_runtime.stop_pump()

    @pytest.mark.asyncio
    async def test_fresh_ptt_read_rechecks_binding_before_dispatch(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        observer = [].append
        radio._bind_authoritative_ptt_observer(provider_generation=9, observer=observer)
        entered = asyncio.Event()
        release = asyncio.Event()
        send = radio._civ_runtime._send_civ_frame_now
        read = radio._request_authoritative_ptt_read

        async def gated_send(frame: bytes, **kwargs: object) -> None:
            entered.set()
            await release.wait()
            await send(frame, **kwargs)

        with patch.object(radio._civ_runtime, "_send_civ_frame_now", gated_send):
            pending = asyncio.create_task(
                read(provider_generation=9, observer=observer)
            )
            await entered.wait()
            radio._unbind_authoritative_ptt_observer()
            release.set()
            with pytest.raises((CommandError, ConnectionError)):
                await pending
        assert mock_transport.sent_packets == []

    @pytest.mark.asyncio
    async def test_fresh_ptt_read_fails_closed_on_callback_cancel_and_reconnect(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        def broken(_observation: tx.ProviderPttObservation) -> None:
            raise asyncio.CancelledError("observer")

        radio._bind_authoritative_ptt_observer(provider_generation=10, observer=broken)
        mock_transport.queue_response(_ptt_response(True))
        with pytest.raises(CommandError, match="authoritative"):
            await radio._request_authoritative_ptt_read(
                provider_generation=10, observer=broken
            )

        observations: list[tx.ProviderPttObservation] = []
        observer = observations.append
        read = radio._request_authoritative_ptt_read
        radio._bind_authoritative_ptt_observer(
            provider_generation=10, observer=observer
        )
        pending = asyncio.create_task(read(provider_generation=10, observer=observer))
        while len(mock_transport.sent_packets) < 2:
            await asyncio.sleep(0)
        radio._civ_runtime.advance_generation("test-reconnect")
        mock_transport.queue_response(_ptt_response(False))
        with pytest.raises(ConnectionError):
            await pending
        assert observations == []


class TestTimeout:
    """Test timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout_on_no_response(self, mock_transport: MockTransport) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.1)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        with pytest.raises(TimeoutError):
            await radio.get_freq()

    @pytest.mark.asyncio
    async def test_deadline_timeout_does_not_always_send_three_attempts(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", timeout=0.2)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        with pytest.raises(TimeoutError):
            await radio.get_freq()

        # Deadline-based retries should stop when overall deadline is exhausted.
        assert len(mock_transport.sent_packets) < 3


class TestDisconnected:
    """Test that operations raise when disconnected."""

    @pytest.mark.asyncio
    async def test_get_frequency_disconnected(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await radio.get_freq()

    @pytest.mark.asyncio
    async def test_set_frequency_disconnected(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await radio.set_freq(14_074_000)

    @pytest.mark.asyncio
    async def test_send_civ_disconnected(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await radio.send_civ(0x03)


class TestSendCiv:
    """Test low-level CI-V access."""

    @pytest.mark.asyncio
    async def test_send_civ(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_freq_response(14_074_000))
        frame = await radio.send_civ(0x03)
        assert frame.command == _CMD_FREQ_GET


class TestConnectedProperty:
    """Test connected property."""

    def test_initially_disconnected(self) -> None:
        radio = IcomRadio("192.168.1.100")
        assert not radio.connected


class TestAckSinkRobustness:
    """Regression tests for fire-and-forget ACK sink behavior."""

    @pytest.mark.asyncio
    async def test_fire_and_forget_missing_ack_does_not_poison_next_ack(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # Fire-and-forget scope enable (ACK intentionally missing)
        ff = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x27, sub=0x10, data=b"\x01"
        )
        await radio._execute_civ_raw(ff, wait_response=False)

        # set_frequency is fire-and-forget — completes without any response.
        await radio.set_freq(7_074_000)

    @pytest.mark.asyncio
    async def test_fire_and_forget_send_failure_rolls_back_sink(self) -> None:
        class FailingTransport(MockTransport):
            async def send_tracked(
                self, data: bytes
            ) -> None:  # pragma: no cover - simple stub
                raise OSError("send failed")

        t = FailingTransport()
        radio = IcomRadio("192.168.1.100")
        radio._ctrl_transport = t
        radio._civ_transport = t
        radio._connected = True

        ff = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x27, sub=0x10, data=b"\x01"
        )
        with pytest.raises(OSError):
            await radio._execute_civ_raw(ff, wait_response=False)

        assert radio._civ_request_tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_cancelled_execute_cleans_pending_waiter(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        cmd = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x03)
        task = asyncio.create_task(radio._execute_civ_raw(cmd))
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert radio._civ_request_tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_pump_uses_current_generation_after_reconnect(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        radio._civ_runtime.start_pump()
        try:
            await radio._civ_runtime.stop_pump()
            radio._civ_runtime.advance_generation("unit-test-reconnect")
            radio._civ_runtime.start_pump()

            mock_transport.queue_response_on_send(1, _freq_response(14_074_000))
            mock_transport.queue_response_on_send(2, _mode_response(Mode.USB))

            assert await radio.get_freq() == 14_074_000
            assert await radio.get_mode_info() == (Mode.USB, 1)
            assert radio._civ_request_tracker.timeout_count == 0
        finally:
            await radio._civ_runtime.stop_pump()


class TestScopeCallbackSafety:
    """Scope callback failures must not break command routing."""

    @pytest.mark.asyncio
    async def test_scope_callback_exception_does_not_break_ack_flow(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        def _bad_callback(_frame) -> None:
            raise RuntimeError("boom")

        radio.on_scope_data(_bad_callback)

        scope_payload = bytes(
            [
                0x00,  # receiver
                0x01,  # seq
                0x01,  # seq_max
                0x01,  # mode=fixed
                *bcd_encode(14_000_000),
                *bcd_encode(14_350_000),
                0x00,  # out_of_range
                0x10,  # one pixel
            ]
        )
        scope_frame = build_civ_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x27,
            sub=0x00,
            data=scope_payload,
        )

        mock_transport.queue_response(_wrap_civ_in_udp(scope_frame))
        mock_transport.queue_response(_ack_response())

        cmd = build_civ_frame(
            IC_7610_ADDR,
            CONTROLLER_ADDR,
            0x05,
            data=bcd_encode(14_074_000),
        )
        resp = await radio._execute_civ_raw(cmd)
        assert resp is not None
        assert resp.command == _CMD_ACK

        # set_frequency is fire-and-forget — RX pump unaffected.
        await radio.set_freq(7_074_000)


class TestAdvancedScopeControls:
    """Advanced scope getters/setters are exposed as maintained radio methods."""

    @pytest.mark.asyncio
    async def test_get_scope_receiver(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_scope_response(0x12, b"\x01"))
        assert await radio.get_scope_receiver() == 1

    @pytest.mark.asyncio
    async def test_set_scope_receiver_updates_radio_state(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_scope_receiver(1)
        assert radio.radio_state.scope_controls.receiver == 1
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_get_scope_mode(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_scope_response(0x14, b"\x00\x03"))
        assert await radio.get_scope_mode() == 3

    @pytest.mark.asyncio
    async def test_set_scope_during_tx_updates_radio_state(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_scope_during_tx(True)
        assert radio.radio_state.scope_controls.during_tx is True
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_get_scope_center_type(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_scope_response(0x1C, b"\x00\x02"))
        assert await radio.get_scope_center_type() == 2

    @pytest.mark.asyncio
    async def test_get_scope_fixed_edge(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(
            _scope_response(
                0x1E,
                b"\x06\x04" + bcd_encode(14_000_000) + bcd_encode(14_350_000),
            )
        )
        bounds = await radio.get_scope_fixed_edge()
        assert bounds.range_index == 6
        assert bounds.edge == 4
        assert bounds.start_hz == 14_000_000
        assert bounds.end_hz == 14_350_000

    @pytest.mark.asyncio
    async def test_set_scope_fixed_edge_updates_radio_state(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_scope_fixed_edge(edge=4, start_hz=14_000_000, end_hz=14_350_000)
        fixed_edge = radio.radio_state.scope_controls.fixed_edge
        assert fixed_edge.range_index == 6
        assert fixed_edge.edge == 4
        assert fixed_edge.start_hz == 14_000_000
        assert fixed_edge.end_hz == 14_350_000
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "response", "field_name", "expected"),
        [
            ("get_scope_dual", _scope_response(0x13, b"\x01"), "dual", True),
            (
                "get_scope_span",
                _scope_response(0x15, b"\x00" + bcd_encode(250_000)),
                "span",
                6,
            ),
            ("get_scope_edge", _scope_response(0x16, b"\x00\x04"), "edge", 4),
            ("get_scope_hold", _scope_response(0x17, b"\x00\x01"), "hold", True),
            (
                "get_scope_ref",
                # -10.5 dB: 10dB=1, 1dB=0, 0.1dB=5 → [rx=0x00, 0x10, 0x50, sign=0x01]
                _scope_response(0x19, b"\x00\x10\x50\x01"),
                "ref_db",
                -10.5,
            ),
            ("get_scope_speed", _scope_response(0x1A, b"\x00\x02"), "speed", 2),
            ("get_scope_during_tx", _scope_response(0x1B, b"\x01"), "during_tx", True),
            ("get_scope_vbw", _scope_response(0x1D, b"\x00\x01"), "vbw_narrow", True),
            ("get_scope_rbw", _scope_response(0x1F, b"\x01\x02"), "rbw", 2),
        ],
    )
    async def test_get_scope_variants_update_radio_state(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        response: bytes,
        field_name: str,
        expected: object,
    ) -> None:
        mock_transport.queue_response(response)
        result = await getattr(radio, method_name)()
        assert result == expected
        assert getattr(radio.radio_state.scope_controls, field_name) == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "kwargs", "field_name", "expected"),
        [
            ("set_scope_dual", {"dual": True}, "dual", True),
            ("set_scope_mode", {"mode": 3}, "mode", 3),
            ("set_scope_span", {"span": 6}, "span", 6),
            ("set_scope_edge", {"edge": 4}, "edge", 4),
            ("set_scope_hold", {"on": True}, "hold", True),
            ("set_scope_ref", {"ref": -10.5}, "ref_db", -10.5),
            ("set_scope_speed", {"speed": 2}, "speed", 2),
            ("set_scope_center_type", {"center_type": 2}, "center_type", 2),
            ("set_scope_vbw", {"narrow": True}, "vbw_narrow", True),
            ("set_scope_rbw", {"rbw": 2}, "rbw", 2),
        ],
    )
    async def test_set_scope_variants_update_radio_state(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        kwargs: dict[str, object],
        field_name: str,
        expected: object,
    ) -> None:
        await getattr(radio, method_name)(**kwargs)
        assert getattr(radio.radio_state.scope_controls, field_name) == expected
        assert len(mock_transport.sent_packets) > 0


class TestScopeSetterReceiverPrefix:
    """MOR-664: SET receiver-prefix must mirror the matching GETTER.

    Live-proven on the real IC-7610 (raw CI-V traces): vbw/rbw GET reads the
    selected receiver (sends a receiver-prefix byte) while the SET wrote WITHOUT
    the prefix — so the radio never reflected the write ("did not react"). The
    fix adds the receiver prefix to set_scope_vbw / set_scope_rbw. Conversely,
    single/dual is a GLOBAL scope setting: get_scope_dual sends NO prefix, but
    the SET was sending one, malforming the write — the fix drops the prefix
    from set_scope_dual so it matches the getter.

    These assertions pin the EXACT CI-V frame bytes (cmd 0x27, sub, optional
    receiver prefix, value) embedded in the UDP-wrapped packet.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("receiver", [0, 1])
    async def test_set_scope_vbw_includes_receiver_prefix(
        self, radio: IcomRadio, mock_transport: MockTransport, receiver: int
    ) -> None:
        """set_scope_vbw sends sub 0x1D with the receiver-prefix byte then value
        (mirrors get_scope_vbw, which passes receiver=)."""
        radio.radio_state.scope_controls.receiver = receiver
        await radio.set_scope_vbw(narrow=True)
        sent = bytes(mock_transport.sent_packets[-1])
        # 0x27 0x1D <receiver> 0x01 0xFD — prefix byte present, value last.
        assert bytes([0x27, 0x1D, receiver, 0x01, 0xFD]) in sent

    @pytest.mark.asyncio
    @pytest.mark.parametrize("receiver", [0, 1])
    async def test_set_scope_rbw_includes_receiver_prefix(
        self, radio: IcomRadio, mock_transport: MockTransport, receiver: int
    ) -> None:
        """set_scope_rbw sends sub 0x1F with the receiver-prefix byte then value
        (mirrors get_scope_rbw, which passes receiver=)."""
        radio.radio_state.scope_controls.receiver = receiver
        await radio.set_scope_rbw(2)
        sent = bytes(mock_transport.sent_packets[-1])
        # 0x27 0x1F <receiver> 0x02 0xFD — prefix byte present, value last.
        assert bytes([0x27, 0x1F, receiver, 0x02, 0xFD]) in sent

    @pytest.mark.asyncio
    @pytest.mark.parametrize("receiver", [0, 1])
    async def test_set_scope_dual_omits_receiver_prefix(
        self, radio: IcomRadio, mock_transport: MockTransport, receiver: int
    ) -> None:
        """set_scope_dual sends sub 0x13 with NO receiver prefix regardless of
        the selected receiver (single/dual is global; matches get_scope_dual's
        prefix-less frame shape)."""
        radio.radio_state.scope_controls.receiver = receiver
        await radio.set_scope_dual(True)
        sent = bytes(mock_transport.sent_packets[-1])
        # Corrected frame: 0x27 0x13 0x01 0xFD (no receiver byte between sub and
        # value). The old buggy frame was 0x27 0x13 <receiver> 0x01.
        assert bytes([0x27, 0x13, 0x01, 0xFD]) in sent
        assert bytes([0x27, 0x13, receiver, 0x01, 0xFD]) not in sent


# ---------------------------------------------------------------------------
# set_mode fire-and-forget (IC-7610 ACK quirk fix)
# ---------------------------------------------------------------------------


class TestSetModeFireAndForget:
    """set_mode is fire-and-forget — no ACK required, no timeout to swallow."""

    @pytest.mark.asyncio
    async def test_set_mode_no_ack_needed(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_mode completes without queuing any radio response."""
        await radio.set_mode(Mode.USB)  # must not raise
        assert radio._last_mode == Mode.USB

    @pytest.mark.asyncio
    async def test_set_mode_string_no_ack_needed(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """String mode variant also completes without a response."""
        await radio.set_mode("USB")
        assert radio._last_mode == Mode.USB

    @pytest.mark.asyncio
    async def test_set_mode_string_case_insensitive(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_mode(" usb ")
        assert radio._last_mode == Mode.USB

    @pytest.mark.asyncio
    async def test_set_mode_string_invalid_raises_value_error(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="Unknown mode"):
            await radio.set_mode("not-a-mode")

    @pytest.mark.asyncio
    async def test_set_mode_updates_last_mode(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_mode updates _last_mode regardless of radio response."""
        await radio.set_mode(Mode.LSB)
        assert radio._last_mode == Mode.LSB

    @pytest.mark.asyncio
    async def test_set_mode_send_failure_propagates(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """A send-level error (e.g. OSError) still propagates from set_mode."""
        with patch.object(radio, "_send_civ_raw", side_effect=OSError("send failed")):
            with pytest.raises(OSError):
                await radio.set_mode(Mode.USB)


# ---------------------------------------------------------------------------
# MOR-1487: hyphenated display labels ("CW-R", "RTTY-R") must coerce to the
# underscored Mode enum tokens (Mode.CW_R, Mode.RTTY_R). The frontend sends
# rig-TOML display labels verbatim; _coerce_mode is the boundary that must
# normalize them.
# ---------------------------------------------------------------------------


class TestCoerceModeHyphenNormalization:
    """Unit tests for ``IcomRadio._coerce_mode`` — no radio/transport needed."""

    def test_coerce_mode_hyphenated_cw_r(self) -> None:
        assert IcomRadio._coerce_mode("CW-R") == Mode.CW_R

    def test_coerce_mode_hyphenated_rtty_r(self) -> None:
        assert IcomRadio._coerce_mode("RTTY-R") == Mode.RTTY_R

    def test_coerce_mode_hyphenated_lowercase_and_whitespace(self) -> None:
        """Hyphen normalization composes with existing case/whitespace tolerance."""
        assert IcomRadio._coerce_mode("  cw-r  ") == Mode.CW_R
        assert IcomRadio._coerce_mode(" rtty-r ") == Mode.RTTY_R

    def test_coerce_mode_mode_enum_passthrough_unchanged(self) -> None:
        """A ``Mode`` instance is returned as-is, no string processing."""
        assert IcomRadio._coerce_mode(Mode.CW_R) is Mode.CW_R

    def test_coerce_mode_underscored_token_unchanged(self) -> None:
        """Existing underscored-token behavior is preserved."""
        assert IcomRadio._coerce_mode("CW_R") == Mode.CW_R
        assert IcomRadio._coerce_mode("RTTY_R") == Mode.RTTY_R

    def test_coerce_mode_plain_token_case_insensitive(self) -> None:
        """Plain (non-hyphenated) tokens keep working, any case/whitespace."""
        assert IcomRadio._coerce_mode(" usb ") == Mode.USB
        assert IcomRadio._coerce_mode("Lsb") == Mode.LSB

    def test_coerce_mode_invalid_still_raises_with_supported_modes_message(
        self,
    ) -> None:
        """Truly unknown mode names still raise with the supported-modes list."""
        with pytest.raises(ValueError, match="Unknown mode") as exc_info:
            IcomRadio._coerce_mode("not-a-mode")
        message = str(exc_info.value)
        assert "Supported modes:" in message
        assert "CW_R" in message
        assert "RTTY_R" in message


# ---------------------------------------------------------------------------
# #48 regression: CI-V timeout isolation
# ---------------------------------------------------------------------------


class TestCivTimeoutIsolation:
    """A CI-V timeout must not corrupt state for subsequent commands (#48)."""

    @pytest.mark.asyncio
    async def test_timeout_does_not_affect_subsequent_command(
        self, mock_transport: MockTransport
    ) -> None:
        """After a CI-V timeout, the next command must succeed independently."""
        radio = IcomRadio("192.168.1.100", timeout=0.1)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        # First command: no response → times out
        with pytest.raises(TimeoutError):
            await radio.get_freq()

        # Tracker must be clean — no stale waiters
        assert radio._civ_request_tracker.pending_count == 0

        # Second command: queue response before calling
        mock_transport.queue_response(_freq_response(7_074_000))
        freq = await radio.get_freq()
        assert freq == 7_074_000

    @pytest.mark.asyncio
    async def test_multiple_timeouts_followed_by_success(
        self, mock_transport: MockTransport
    ) -> None:
        """Multiple consecutive timeouts do not corrupt tracker state."""
        radio = IcomRadio("192.168.1.100", timeout=0.1)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        # Two timeouts in a row
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await radio.get_freq()

        assert radio._civ_request_tracker.pending_count == 0

        # Then a successful command
        mock_transport.queue_response(_freq_response(14_074_000))
        freq = await radio.get_freq()
        assert freq == 14_074_000

    @pytest.mark.asyncio
    async def test_timeout_then_different_command_succeeds(
        self, mock_transport: MockTransport
    ) -> None:
        """A timeout on get_frequency does not block a subsequent set_frequency."""
        radio = IcomRadio("192.168.1.100", timeout=0.1)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        # get_frequency times out
        with pytest.raises(TimeoutError):
            await radio.get_freq()

        assert radio._civ_request_tracker.pending_count == 0

        # set_frequency is fire-and-forget — succeeds without a response
        await radio.set_freq(14_074_000)  # must not raise


# ---------------------------------------------------------------------------
# Dual watch
# ---------------------------------------------------------------------------


def _dual_watch_response(on: bool) -> bytes:
    """Build a CI-V dual-watch query response (0x07 0xC2 <on>) wrapped in UDP."""
    from rigplane.commands import build_civ_frame

    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x07, data=bytes([0xC2, 0x01 if on else 0x00])
    )
    return _wrap_civ_in_udp(civ)


class TestDualWatch:
    """Tests for IcomRadio.get_dual_watch / set_dual_watch."""

    @pytest.mark.asyncio
    async def test_get_dual_watch_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_dual_watch_response(True))
        result = await radio.get_dual_watch()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_dual_watch_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_dual_watch_response(False))
        result = await radio.get_dual_watch()
        assert result is False

    @pytest.mark.asyncio
    async def test_set_dual_watch_sends_packet(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_dual_watch is fire-and-forget — completes without a radio response."""
        await radio.set_dual_watch(True)
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_set_dual_watch_off_sends_packet(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_dual_watch(False)
        assert len(mock_transport.sent_packets) > 0


# ---------------------------------------------------------------------------
# Speech, Transceiver ID, XFC Status
# ---------------------------------------------------------------------------


def _bool_response_1c(sub: int, val: bool) -> bytes:
    """Build a mock 0x1C response from the radio, wrapped in UDP."""
    from rigplane.commands import build_civ_frame

    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x1C,
        sub=sub,
        data=bytes([0x01 if val else 0x00]),
    )
    return _wrap_civ_in_udp(civ)


def _transceiver_id_response(model_id: int = 0x98) -> bytes:
    """Build a mock 0x19 0x00 response from the radio, wrapped in UDP."""
    from rigplane.commands import build_civ_frame

    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x19,
        sub=0x00,
        data=bytes([model_id]),
    )
    return _wrap_civ_in_udp(civ)


class TestSpeechTransceiverIdXfc:
    """Tests for speech, get_transceiver_id, get/set_xfc_status."""

    @pytest.fixture
    def mock_transport(self) -> MockTransport:
        return MockTransport()

    @pytest.fixture
    def radio(self, mock_transport: MockTransport) -> IcomRadio:
        r = IcomRadio("192.168.1.100")
        r._connected = True
        r._radio_addr = 0x98
        r._civ_transport = mock_transport
        r._ctrl_transport = mock_transport
        return r

    @pytest.mark.asyncio
    async def test_speech_sends_packet(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """speech() is fire-and-forget — completes without response."""
        await radio.get_speech(0)
        assert len(mock_transport.sent_packets) > 0
        assert b"\x13\x00" in mock_transport.sent_packets[-1]

    @pytest.mark.asyncio
    async def test_speech_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.get_speech(1)
        assert b"\x13\x01" in mock_transport.sent_packets[-1]

    @pytest.mark.asyncio
    async def test_speech_mode(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.get_speech(2)
        assert b"\x13\x02" in mock_transport.sent_packets[-1]

    @pytest.mark.asyncio
    async def test_get_transceiver_id(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_transceiver_id_response(0x98))
        result = await radio.get_transceiver_id()
        assert result == 0x98

    @pytest.mark.asyncio
    async def test_get_xfc_status_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_bool_response_1c(0x02, True))
        result = await radio.get_xfc_status()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_xfc_status_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_bool_response_1c(0x02, False))
        result = await radio.get_xfc_status()
        assert result is False

    @pytest.mark.asyncio
    async def test_set_xfc_status_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_xfc_status(True)
        assert len(mock_transport.sent_packets) > 0
        assert b"\x1c\x02\x01" in mock_transport.sent_packets[-1]

    @pytest.mark.asyncio
    async def test_set_xfc_status_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_xfc_status(False)
        assert len(mock_transport.sent_packets) > 0
        assert b"\x1c\x02\x00" in mock_transport.sent_packets[-1]


# ---------------------------------------------------------------------------
# Issue #56: state cache populated from unsolicited CI-V frames
# ---------------------------------------------------------------------------


class TestStateCacheFromUnsolicitedFrames:
    """_update_state_cache_from_frame populates cache from radio-pushed frames."""

    def _make_radio(self) -> IcomRadio:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        return radio

    def test_freq_frame_updates_cache(self) -> None:
        """A frequency frame (cmd 0x03) updates the freq cache."""
        radio = self._make_radio()
        assert radio.state_cache.freq_ts == 0.0
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x03,
            data=bcd_encode(14_200_000),
        )
        radio._update_state_cache_from_frame(frame)
        assert radio.state_cache.freq == 14_200_000
        assert radio.state_cache.freq_ts > 0.0

    def test_mode_frame_updates_cache(self) -> None:
        """A mode frame (cmd 0x04) updates the mode cache."""
        radio = self._make_radio()
        assert radio.state_cache.mode_ts == 0.0
        # cmd 0x04: data = [mode_byte, filter_byte]; mode 0x00=LSB, filter 0x01=FIL1
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x04,
            data=bytes([0x00, 0x01]),
        )
        radio._update_state_cache_from_frame(frame)
        assert radio.state_cache.mode == "LSB"
        assert radio.state_cache.filter_width == 1
        assert radio.state_cache.mode_ts > 0.0

    def test_ptt_frame_updates_cache(self) -> None:
        """A PTT frame (cmd 0x1C sub 0x00, data=0x01) updates ptt cache."""
        radio = self._make_radio()
        assert radio.state_cache.ptt_ts == 0.0
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x1C,
            sub=0x00,
            data=bytes([0x01]),
        )
        radio._update_state_cache_from_frame(frame)
        assert radio.state_cache.ptt is True
        assert radio.state_cache.ptt_ts > 0.0

    def test_ptt_off_frame_updates_cache(self) -> None:
        """A PTT frame with data=0x00 clears the ptt cache."""
        radio = self._make_radio()
        radio.state_cache.update_ptt(True)
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x1C,
            sub=0x00,
            data=bytes([0x00]),
        )
        radio._update_state_cache_from_frame(frame)
        assert radio.state_cache.ptt is False

    def test_unknown_frame_is_ignored_safely(self) -> None:
        """Frames with unrecognised commands are silently ignored."""
        radio = self._make_radio()
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0xFF,
            data=b"\x01\x02",
        )
        radio._update_state_cache_from_frame(frame)  # must not raise
        assert radio.state_cache.freq_ts == 0.0


# ---------------------------------------------------------------------------
# Issue #56: GET commands fall back to cache on timeout
# ---------------------------------------------------------------------------


class TestGetFallbackToCache:
    """GET commands return cached values instead of raising on timeout."""

    @pytest.mark.asyncio
    async def test_get_frequency_returns_cache_on_timeout(
        self, mock_transport: MockTransport
    ) -> None:
        """get_frequency returns cached freq when radio is silent."""
        radio = IcomRadio("192.168.1.100", timeout=0.05)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_freq(7_200_000)

        freq = await radio.get_freq()
        assert freq == 7_200_000

    @pytest.mark.asyncio
    async def test_get_frequency_raises_when_cache_empty(
        self, mock_transport: MockTransport
    ) -> None:
        """get_frequency raises TimeoutError when cache is empty and radio is silent."""
        radio = IcomRadio("192.168.1.100", timeout=0.05)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        with pytest.raises(TimeoutError):
            await radio.get_freq()

    @pytest.mark.asyncio
    async def test_get_mode_info_returns_cache_on_timeout(
        self, mock_transport: MockTransport
    ) -> None:
        """get_mode_info returns cached mode/filter when radio is silent."""
        radio = IcomRadio("192.168.1.100", timeout=0.05)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_mode("CW", 2)

        mode, filt = await radio.get_mode_info()
        assert mode == Mode.CW
        assert filt == 2

    @pytest.mark.asyncio
    async def test_get_mode_info_raises_when_cache_empty(
        self, mock_transport: MockTransport
    ) -> None:
        """get_mode_info raises TimeoutError when cache is empty and radio is silent."""
        radio = IcomRadio("192.168.1.100", timeout=0.05)
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True

        with pytest.raises(TimeoutError):
            await radio.get_mode_info()


# ---------------------------------------------------------------------------
# Issue #63: GET fallbacks respect cache TTL
# ---------------------------------------------------------------------------


class TestGetFallbackCacheTTL:
    """GET commands raise TimeoutError when cache is stale (exceeds TTL)."""

    @pytest.mark.asyncio
    async def test_get_frequency_raises_when_cache_stale(
        self, mock_transport: MockTransport
    ) -> None:
        """get_frequency raises TimeoutError when cached value is older than TTL."""
        radio = IcomRadio("192.168.1.100", timeout=0.05, cache_ttl_s={"freq": 10.0})
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_freq(7_200_000)
        # Back-date to make it stale
        radio.state_cache.freq_ts = radio.state_cache.freq_ts - 20.0

        with pytest.raises(TimeoutError):
            await radio.get_freq()

    @pytest.mark.asyncio
    async def test_get_frequency_returns_cache_within_ttl(
        self, mock_transport: MockTransport
    ) -> None:
        """get_frequency returns cached value when it is within TTL."""
        radio = IcomRadio("192.168.1.100", timeout=0.05, cache_ttl_s={"freq": 10.0})
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_freq(7_200_000)

        freq = await radio.get_freq()
        assert freq == 7_200_000

    @pytest.mark.asyncio
    async def test_get_mode_info_raises_when_cache_stale(
        self, mock_transport: MockTransport
    ) -> None:
        """get_mode_info raises TimeoutError when cached value is older than TTL."""
        radio = IcomRadio("192.168.1.100", timeout=0.05, cache_ttl_s={"mode": 10.0})
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_mode("CW", 2)
        radio.state_cache.mode_ts = radio.state_cache.mode_ts - 20.0

        with pytest.raises(TimeoutError):
            await radio.get_mode_info()

    @pytest.mark.asyncio
    async def test_get_mode_info_returns_cache_within_ttl(
        self, mock_transport: MockTransport
    ) -> None:
        """get_mode_info returns cached value when it is within TTL."""
        radio = IcomRadio("192.168.1.100", timeout=0.05, cache_ttl_s={"mode": 10.0})
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_mode("CW", 2)

        mode, filt = await radio.get_mode_info()
        assert mode == Mode.CW
        assert filt == 2

    @pytest.mark.asyncio
    async def test_get_power_raises_when_cache_stale(
        self, mock_transport: MockTransport
    ) -> None:
        """get_power raises TimeoutError when cached value is older than TTL."""
        radio = IcomRadio("192.168.1.100", timeout=0.05, cache_ttl_s={"rf_power": 30.0})
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_rf_power(128 / 255.0)
        radio.state_cache.rf_power_ts = radio.state_cache.rf_power_ts - 60.0

        with pytest.raises(TimeoutError):
            await radio.get_rf_power()

    @pytest.mark.asyncio
    async def test_get_power_returns_cache_within_ttl(
        self, mock_transport: MockTransport
    ) -> None:
        """get_power returns cached value when it is within TTL."""
        radio = IcomRadio("192.168.1.100", timeout=0.05, cache_ttl_s={"rf_power": 30.0})
        radio._ctrl_transport = mock_transport
        radio._civ_transport = mock_transport
        radio._connected = True
        radio.state_cache.update_rf_power(128 / 255.0)

        level = await radio.get_rf_power()
        assert level == 128

    @pytest.mark.asyncio
    async def test_cache_ttl_s_overrides_default_per_field(
        self, mock_transport: MockTransport
    ) -> None:
        """cache_ttl_s merges with defaults, overriding individual fields."""
        # Only override freq TTL; mode/rf_power keep defaults.
        radio = IcomRadio("192.168.1.100", timeout=0.05, cache_ttl_s={"freq": 1.0})
        assert radio._cache_ttl_freq == 1.0
        assert radio._cache_ttl_mode == 10.0
        assert radio._cache_ttl_rf_power == 30.0


# ---------------------------------------------------------------------------
# Issue #56: SET commands update the state cache optimistically
# ---------------------------------------------------------------------------


class TestSetCommandsUpdateCache:
    """SET commands update the state cache without waiting for ACK."""

    @pytest.mark.asyncio
    async def test_set_frequency_updates_state_cache(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_frequency updates the frequency cache immediately."""
        await radio.set_freq(21_074_000)
        assert radio.state_cache.freq == 21_074_000
        assert radio.state_cache.freq_ts > 0.0

    @pytest.mark.asyncio
    async def test_set_mode_updates_state_cache(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_mode updates the mode cache immediately."""
        await radio.set_mode(Mode.CW)
        assert radio.state_cache.mode == "CW"
        assert radio.state_cache.mode_ts > 0.0

    @pytest.mark.asyncio
    async def test_rapid_set_frequency_does_not_block(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Rapid consecutive set_frequency calls are all fire-and-forget."""
        for freq in [7_000_000, 7_100_000, 7_200_000]:
            await radio.set_freq(freq)
        # Cache holds the last value sent.
        assert radio.state_cache.freq == 7_200_000


class TestDspLevelParity:
    """Test high-level DSP/level parity methods."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "sub", "value", "receiver"),
        [
            ("get_apf_type_level", 0x05, 90, 1),
            ("get_nr_level", 0x06, 91, 1),
            ("get_pbt_inner", 0x07, 92, 1),
            ("get_pbt_outer", 0x08, 93, 1),
            ("get_notch_filter", 0x0D, 96, 1),
            ("get_nb_level", 0x12, 94, 1),
            ("get_digisel_shift", 0x13, 95, 1),
        ],
    )
    async def test_get_cmd29_dsp_level(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        sub: int,
        value: int,
        receiver: int,
    ) -> None:
        mock_transport.queue_response(_level_response(sub, value, receiver=receiver))

        method = getattr(radio, method_name)
        assert await method(receiver=receiver) == value

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "sub", "value"),
        [
            ("get_mic_gain", 0x0B, 101),
            ("get_notch_filter", 0x0D, 102),
            ("get_compressor_level", 0x0E, 103),
            ("get_break_in_delay", 0x0F, 104),
            ("get_drive_gain", 0x14, 105),
            ("get_monitor_gain", 0x15, 106),
            ("get_vox_gain", 0x16, 107),
            ("get_anti_vox_gain", 0x17, 108),
        ],
    )
    async def test_get_direct_dsp_level(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        sub: int,
        value: int,
    ) -> None:
        mock_transport.queue_response(_level_response(sub, value))

        method = getattr(radio, method_name)
        assert await method() == value

    @pytest.mark.asyncio
    async def test_get_cw_pitch_converts_raw_level(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_level_response(0x09, 128))
        assert await radio.get_cw_pitch() == 600

    @pytest.mark.asyncio
    async def test_get_key_speed_converts_raw_level(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_level_response(0x0C, 146))
        assert await radio.get_key_speed() == 30

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "prefix", "payload", "expected"),
        [
            ("get_ref_adjust", b"\x00\x70", b"\x05\x11", 511),
            ("get_dash_ratio", b"\x02\x28", b"\x45", 45),
            ("get_nb_depth", b"\x02\x90", b"\x09", 9),
            ("get_nb_width", b"\x02\x91", b"\x02\x55", 255),
        ],
    )
    async def test_get_ctl_mem_dsp_level(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        prefix: bytes,
        payload: bytes,
        expected: int,
    ) -> None:
        mock_transport.queue_response(_ctl_mem_response(0x05, payload, prefix=prefix))

        method = getattr(radio, method_name)
        assert await method() == expected

    @pytest.mark.asyncio
    async def test_get_af_mute_reads_bool(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_ctl_mem_response(0x09, b"\x01", receiver=1))
        assert await radio.get_af_mute(receiver=1) is True

    @pytest.mark.asyncio
    async def test_set_cw_pitch_sends_scaled_level(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_cw_pitch(600)
        sent = mock_transport.sent_packets[-1]
        assert b"\x14\x09\x01\x28\xfd" in sent

    @pytest.mark.asyncio
    async def test_set_key_speed_sends_scaled_level(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_key_speed(30)
        sent = mock_transport.sent_packets[-1]
        assert b"\x14\x0c\x01\x46\xfd" in sent

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "value", "expected_tail"),
        [
            ("set_apf_type_level", 120, b"\x29\x01\x14\x05\x01\x20\xfd"),
            ("set_nr_level", 121, b"\x29\x01\x14\x06\x01\x21\xfd"),
            ("set_pbt_inner", 122, b"\x29\x01\x14\x07\x01\x22\xfd"),
            ("set_pbt_outer", 123, b"\x29\x01\x14\x08\x01\x23\xfd"),
            ("set_notch_filter", 131, b"\x29\x01\x14\x0d\x01\x31\xfd"),
            ("set_nb_level", 124, b"\x29\x01\x14\x12\x01\x24\xfd"),
            ("set_digisel_shift", 125, b"\x29\x01\x14\x13\x01\x25\xfd"),
        ],
    )
    async def test_set_cmd29_dsp_level(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        value: int,
        expected_tail: bytes,
    ) -> None:
        method = getattr(radio, method_name)
        await method(value, receiver=1)
        assert mock_transport.sent_packets[-1].endswith(expected_tail)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "value", "expected_tail"),
        [
            ("set_mic_gain", 130, b"\x14\x0b\x01\x30\xfd"),
            ("set_notch_filter", 131, b"\x14\x0d\x01\x31\xfd"),
            ("set_compressor_level", 132, b"\x14\x0e\x01\x32\xfd"),
            ("set_break_in_delay", 133, b"\x14\x0f\x01\x33\xfd"),
            ("set_drive_gain", 134, b"\x14\x14\x01\x34\xfd"),
            ("set_monitor_gain", 135, b"\x14\x15\x01\x35\xfd"),
            ("set_vox_gain", 136, b"\x14\x16\x01\x36\xfd"),
            ("set_anti_vox_gain", 137, b"\x14\x17\x01\x37\xfd"),
        ],
    )
    async def test_set_direct_dsp_level(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        value: int,
        expected_tail: bytes,
    ) -> None:
        method = getattr(radio, method_name)
        await method(value)
        assert mock_transport.sent_packets[-1].endswith(expected_tail)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "value", "expected_tail"),
        [
            ("set_ref_adjust", 511, b"\x1a\x05\x00\x70\x05\x11\xfd"),
            ("set_dash_ratio", 45, b"\x1a\x05\x02\x28\x45\xfd"),
            ("set_nb_depth", 9, b"\x1a\x05\x02\x90\x09\xfd"),
            ("set_nb_width", 255, b"\x1a\x05\x02\x91\x02\x55\xfd"),
        ],
    )
    async def test_set_ctl_mem_dsp_level(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        value: int,
        expected_tail: bytes,
    ) -> None:
        method = getattr(radio, method_name)
        await method(value)
        assert mock_transport.sent_packets[-1].endswith(expected_tail)

    @pytest.mark.asyncio
    async def test_set_af_mute_sends_cmd29_bool(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_af_mute(True, receiver=1)
        assert mock_transport.sent_packets[-1].endswith(b"\x29\x01\x1a\x09\x01\xfd")

    @pytest.mark.asyncio
    async def test_set_data_mode_sub_receiver_plain_when_no_cmd29_route(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # IC-7610 profile omits 0x1A 0x06 from cmd29 (wfview Command29=false): sub
        # DATA mode uses VFO select + plain 0x1A 0x06. Active SUB avoids extra VFO traffic.
        radio._radio_state.active = "SUB"
        mock_transport.queue_response(_ack_response())
        await radio.set_data_mode(3, receiver=1)
        assert mock_transport.sent_packets[-1].endswith(b"\x1a\x06\x03\xfd")

    @pytest.mark.asyncio
    async def test_set_cw_pitch_rejects_out_of_range(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="300-900"):
            await radio.set_cw_pitch(299)

    @pytest.mark.asyncio
    async def test_set_key_speed_rejects_out_of_range(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="6-48"):
            await radio.set_key_speed(49)

    @pytest.mark.asyncio
    async def test_set_ref_adjust_rejects_out_of_range(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="0-511"):
            await radio.set_ref_adjust(512)

    @pytest.mark.asyncio
    async def test_set_nb_width_rejects_out_of_range(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        with pytest.raises(ValueError, match="0-255"):
            await radio.set_nb_width(256)


class TestNbDepthWidthPollerDispatch:
    """Poller-level NB depth/width SET against the REAL IcomRadio signature.

    NB depth/width are GLOBAL menu items (0x1A 05 02 90 / 0x1A 05 02 91),
    so ``IcomRadio.set_nb_depth``/``set_nb_width`` take ``(self, value)`` with
    no ``receiver`` parameter (unlike per-receiver ``set_nb_level``). The
    poller's ``_execute`` must therefore call them WITHOUT ``receiver``.
    Driving the real radio (not an arg-swallowing AsyncMock) catches the
    ``TypeError`` that previously silenced these sliders (MOR-491).
    """

    @pytest.mark.asyncio
    async def test_execute_set_nb_depth_sends_global_frame(
        self, mock_transport: MockTransport
    ) -> None:
        from rigplane.core.state_pipeline_contracts import FieldPath
        from rigplane.core.state_store import StateStore
        from rigplane.web.radio_poller import (
            CommandQueue,
            RadioPoller,
            SetNbDepth,
        )

        # Post-set write-through readback (MOR-491-B): the radio reports back
        # depth=4 even though 5 was sent (clamp/quantization). The web state
        # must reflect the radio's REAL value, not the sent value. The readback
        # response is released only after the GET send (send #2) so the
        # fire-and-forget SET (send #1) does not consume it.
        radio = IcomRadio("192.168.1.100", timeout=0.5)
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        mock_transport.queue_response_on_send(
            2, _ctl_mem_response(0x05, b"\x04", prefix=b"\x02\x90")
        )
        store = StateStore()
        poller = RadioPoller(radio, CommandQueue(), state_store=store)
        await poller._execute(SetNbDepth(level=5, receiver=0))  # noqa: SLF001
        radio._connected = False
        assert any(
            pkt.endswith(b"\x1a\x05\x02\x90\x05\xfd")
            for pkt in mock_transport.sent_packets
        )
        assert (
            store.snapshot()
            .field(FieldPath.global_("operator_controls", "nb_depth"))
            .value
            == 4
        )

    @pytest.mark.asyncio
    async def test_execute_set_nb_width_sends_global_frame(
        self, mock_transport: MockTransport
    ) -> None:
        from rigplane.core.state_pipeline_contracts import FieldPath
        from rigplane.core.state_store import StateStore
        from rigplane.web.radio_poller import (
            CommandQueue,
            RadioPoller,
            SetNbWidth,
        )

        # Post-set write-through readback (MOR-491-B): radio reports 200 even
        # though 255 was sent. The web state reflects the radio's real value.
        # Release the readback response only after the GET send (send #2).
        radio = IcomRadio("192.168.1.100", timeout=0.5)
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        mock_transport.queue_response_on_send(
            2, _ctl_mem_response(0x05, b"\x02\x00", prefix=b"\x02\x91")
        )
        store = StateStore()
        poller = RadioPoller(radio, CommandQueue(), state_store=store)
        await poller._execute(SetNbWidth(level=255, receiver=0))  # noqa: SLF001
        radio._connected = False
        assert any(
            pkt.endswith(b"\x1a\x05\x02\x91\x02\x55\xfd")
            for pkt in mock_transport.sent_packets
        )
        assert (
            store.snapshot()
            .field(FieldPath.global_("operator_controls", "nb_width"))
            .value
            == 200
        )


class TestOperatorToggleParity:
    """Test high-level operator toggle/status parity methods."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "response", "expected"),
        [
            (
                "get_s_meter_sql_status",
                _meter_status_response(0x01, 0x01, receiver=1),
                True,
            ),
            ("get_overflow_status", _meter_status_response(0x07, 0x01), True),
            ("get_auto_notch", _function_response(0x41, b"\x01", receiver=1), True),
            ("get_compressor", _function_response(0x44, b"\x01"), True),
            ("get_monitor", _function_response(0x45, b"\x01"), True),
            ("get_vox", _function_response(0x46, b"\x01"), True),
            ("get_manual_notch", _function_response(0x48, b"\x01", receiver=1), True),
            (
                "get_twin_peak_filter",
                _function_response(0x4F, b"\x01", receiver=1),
                True,
            ),
            ("get_dial_lock", _function_response(0x50, b"\x01"), True),
        ],
    )
    async def test_get_bool_operator_toggle(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        response: bytes,
        expected: bool,
    ) -> None:
        mock_transport.queue_response(response)

        method = getattr(radio, method_name)
        kwargs = (
            {"receiver": 1}
            if method_name
            in {
                "get_s_meter_sql_status",
                "get_auto_notch",
                "get_manual_notch",
                "get_twin_peak_filter",
            }
            else {}
        )
        assert await method(**kwargs) is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "response", "expected"),
        [
            (
                "get_agc",
                # MOR-1537: IC-7610 declares a cmd29 route for 0x16/0x12, so
                # get_agc(receiver=0) now wraps (previously it derived
                # command29 from receiver != MAIN and never wrapped MAIN).
                _function_response(0x12, b"\x03", receiver=0),
                AgcMode.SLOW,
            ),
            (
                "get_audio_peak_filter",
                _function_response(0x32, b"\x02", receiver=1),
                AudioPeakFilter.MID,
            ),
            ("get_break_in", _function_response(0x47, b"\x02"), BreakInMode.FULL),
            (
                "get_filter_shape",
                _function_response(0x56, b"\x01", receiver=1),
                FilterShape.SOFT,
            ),
            (
                "get_ssb_tx_bandwidth",
                _function_response(0x58, b"\x02"),
                SsbTxBandwidth.NAR,
            ),
        ],
    )
    async def test_get_enum_operator_toggle(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        response: bytes,
        expected: object,
    ) -> None:
        mock_transport.queue_response(response)

        method = getattr(radio, method_name)
        kwargs = (
            {"receiver": 1}
            if method_name in {"get_audio_peak_filter", "get_filter_shape"}
            else {}
        )
        assert await method(**kwargs) == expected


class TestTransceiverStatusParity:
    """Test high-level transceiver status parity methods."""

    @pytest.mark.asyncio
    async def test_get_band_edge_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(
            _wrap_civ_in_udp(
                build_civ_frame(
                    CONTROLLER_ADDR,
                    IC_7610_ADDR,
                    0x02,
                    data=bcd_encode(14_074_000),
                )
            )
        )
        assert await radio.get_band_edge_freq() == 14_074_000

    @pytest.mark.asyncio
    async def test_get_various_squelch(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_meter_status_response(0x05, 0x01, receiver=1))
        assert await radio.get_various_squelch(receiver=1) is True

    @pytest.mark.asyncio
    async def test_get_agc_time_constant_reads_single_byte_bcd(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
    ) -> None:
        mock_transport.queue_response(_ctl_mem_response(0x04, b"\x13", receiver=1))
        assert await radio.get_agc_time_constant(receiver=1) == 13

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "value", "kwargs", "expected_tail"),
        [
            ("set_agc", AgcMode.MID, {}, b"\x16\x12\x02\xfd"),
            (
                "set_audio_peak_filter",
                AudioPeakFilter.NAR,
                {"receiver": 1},
                b"\x29\x01\x16\x32\x03\xfd",
            ),
            ("set_auto_notch", True, {"receiver": 1}, b"\x29\x01\x16\x41\x01\xfd"),
            ("set_compressor", True, {}, b"\x16\x44\x01\xfd"),
            ("set_monitor", False, {}, b"\x16\x45\x00\xfd"),
            ("set_vox", True, {}, b"\x16\x46\x01\xfd"),
            ("set_break_in", BreakInMode.SEMI, {}, b"\x16\x47\x01\xfd"),
            ("set_manual_notch", True, {"receiver": 1}, b"\x29\x01\x16\x48\x01\xfd"),
            (
                "set_twin_peak_filter",
                False,
                {"receiver": 1},
                b"\x29\x01\x16\x4f\x00\xfd",
            ),
            ("set_dial_lock", True, {}, b"\x16\x50\x01\xfd"),
            (
                "set_filter_shape",
                FilterShape.SHARP,
                {"receiver": 1},
                b"\x29\x01\x16\x56\x00\xfd",
            ),
            (
                "set_ssb_tx_bandwidth",
                SsbTxBandwidth.MID,
                {},
                b"\x16\x58\x01\xfd",
            ),
            (
                "set_agc_time_constant",
                13,
                {"receiver": 1},
                b"\x29\x01\x1a\x04\x13\xfd",
            ),
        ],
    )
    async def test_set_operator_toggle(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        value: object,
        kwargs: dict[str, int],
        expected_tail: bytes,
    ) -> None:
        method = getattr(radio, method_name)
        await method(value, **kwargs)
        assert mock_transport.sent_packets[-1].endswith(expected_tail)


class TestAgcDomainValidation:
    """MOR-1522: the AGC mode domain is profile data, not a universal enum.

    IC-7300/IC-7610 declare FAST/MID/SLOW only (no OFF); the X6200 declares
    OFF/FAST/SLOW/AUTO. ``set_agc`` must accept exactly what the profile
    declares for that radio and reject anything else with a visible error,
    not a silent write of an illegal byte.
    """

    @pytest.mark.asyncio
    async def test_ic7300_rejects_off_not_in_its_domain(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7300")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        with pytest.raises(ValueError, match=r"AGC mode must be one of"):
            await radio.set_agc(0)
        assert mock_transport.sent_packets == []
        radio._connected = False

    @pytest.mark.asyncio
    async def test_ic7300_accepts_its_declared_domain(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7300")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        for legal in (1, 2, 3):
            await radio.set_agc(legal)
        assert len(mock_transport.sent_packets) == 3
        radio._connected = False

    @pytest.mark.asyncio
    async def test_x6200_accepts_declared_off_that_ic7300_lacks(
        self, mock_transport: MockTransport
    ) -> None:
        """The X6200's declared OFF=0 must not be rejected just because it
        falls outside the IC-7610 ``AgcMode`` enum (FAST=1/MID=2/SLOW=3)."""
        radio = IcomRadio("192.168.1.100", model="X6200")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        await radio.set_agc(0)
        assert mock_transport.sent_packets[-1].endswith(b"\x16\x12\x00\xfd")
        radio._connected = False

    @pytest.mark.asyncio
    async def test_x6200_rejects_value_outside_its_declared_domain(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", model="X6200")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        with pytest.raises(ValueError, match=r"AGC mode must be one of"):
            await radio.set_agc(9)
        assert mock_transport.sent_packets == []
        radio._connected = False


_X6200_ADDR = 0xA4


def _x6200_function_response(sub: int, payload: bytes) -> bytes:
    """Build a plain (no cmd29) CI-V 0x16 response from an X6200.

    X6200 declares no ``[cmd29]`` routes, so its responses (unlike the
    IC-7610 fixture's) come from ``civ_addr = 0xA4``, not ``IC_7610_ADDR``
    — the receive path drops any frame whose ``from_addr`` doesn't match
    the connected radio's own address.
    """
    civ = build_civ_frame(CONTROLLER_ADDR, _X6200_ADDR, 0x16, sub=sub, data=payload)
    return _wrap_civ_in_udp(civ)


class TestAgcReadPath:
    """MOR-1529: ``get_agc`` must not cast the read value through the
    IC-7610-shaped ``AgcMode`` enum (FAST=1/MID=2/SLOW=3).

    The X6200 declares a different domain (OFF=0/FAST=1/SLOW=2/AUTO=3) —
    ``AgcMode(0)`` raises ``ValueError`` (0 is not a member) and
    ``AgcMode(2)``/``AgcMode(3)`` silently mislabel X6200's SLOW/AUTO as the
    IC-7610's MID/SLOW. The fix returns the raw profile-validated int;
    label mapping is data-side (``[agc].labels``), not a hardcoded enum cast.
    """

    @pytest.mark.asyncio
    async def test_x6200_reports_declared_off_without_raising(
        self, mock_transport: MockTransport
    ) -> None:
        """AgcMode has no OFF member — casting X6200's real OFF=0 reading
        through it would raise, even though OFF is a legal X6200 value."""
        radio = IcomRadio("192.168.1.100", model="X6200")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        mock_transport.queue_response(_x6200_function_response(0x12, b"\x00"))
        assert await radio.get_agc() == 0
        radio._connected = False

    @pytest.mark.asyncio
    async def test_x6200_reports_declared_auto_as_plain_int_not_mislabeled(
        self, mock_transport: MockTransport
    ) -> None:
        """X6200 index 3 means AUTO, not the IC-7610's SLOW — the return
        must be the raw int 3, not an ``AgcMode.SLOW`` enum member (whose
        ``.name`` would present the wrong label downstream)."""
        radio = IcomRadio("192.168.1.100", model="X6200")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        mock_transport.queue_response(_x6200_function_response(0x12, b"\x03"))
        result = await radio.get_agc()
        assert result == 3
        assert type(result) is int
        radio._connected = False

    @pytest.mark.asyncio
    async def test_x6200_rejects_reading_outside_its_declared_domain(
        self, mock_transport: MockTransport
    ) -> None:
        """A raw value the radio reports that falls outside the profile's
        declared ``[agc] modes`` must raise, not be silently returned."""
        radio = IcomRadio("192.168.1.100", model="X6200")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        mock_transport.queue_response(_x6200_function_response(0x12, b"\x09"))
        with pytest.raises(ValueError, match=r"AGC mode.*not in declared domain"):
            await radio.get_agc()
        radio._connected = False


class TestPreampDomainValidation:
    """MOR-1523: the preamp level domain is profile data, not a universal
    3-state enum.

    IC-7300 declares OFF/P.AMP1/P.AMP2 (0/1/2); the X6200 only declares
    OFF/P.AMP1 (0/1) — it has no second preamp stage. ``set_preamp`` must
    accept exactly what the profile declares for that radio and reject
    anything else with a visible error, not a silent write of an illegal
    byte (mirrors MOR-1522's ``set_agc`` fix).
    """

    @pytest.mark.asyncio
    async def test_ic7300_rejects_value_outside_its_declared_domain(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7300")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        with pytest.raises(ValueError, match=r"Preamp level must be one of"):
            await radio.set_preamp(3)
        assert mock_transport.sent_packets == []
        radio._connected = False

    @pytest.mark.asyncio
    async def test_ic7300_accepts_its_declared_domain(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-7300")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        for legal in (0, 1, 2):
            await radio.set_preamp(legal)
        assert len(mock_transport.sent_packets) == 3
        radio._connected = False

    @pytest.mark.asyncio
    async def test_x6200_accepts_its_declared_domain(
        self, mock_transport: MockTransport
    ) -> None:
        radio = IcomRadio("192.168.1.100", model="X6200")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        await radio.set_preamp(1)
        assert mock_transport.sent_packets[-1].endswith(b"\x16\x02\x01\xfd")
        radio._connected = False

    @pytest.mark.asyncio
    async def test_x6200_rejects_second_preamp_stage_that_ic7300_has(
        self, mock_transport: MockTransport
    ) -> None:
        """The X6200 has no P.AMP2 hardware stage — its declared domain is
        [0, 1], unlike IC-7300's [0, 1, 2]. Level 2 must be rejected."""
        radio = IcomRadio("192.168.1.100", model="X6200")
        radio._civ_transport = mock_transport
        radio._ctrl_transport = mock_transport
        radio._connected = True
        with pytest.raises(ValueError, match=r"Preamp level must be one of"):
            await radio.set_preamp(2)
        assert mock_transport.sent_packets == []
        radio._connected = False


class TestBreakInModeRoundTrip:
    """Issue #1100 — CwControlCapable.get_break_in/set_break_in type contract.

    Both Icom and Yaesu backends must agree on the :class:`BreakInMode`
    enum return type. Icom exposes the full 3-state enum; Yaesu maps
    ``False`` ↔ ``OFF`` and ``True`` ↔ ``SEMI`` while keeping
    bool-compatibility at runtime (``IntEnum``).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (b"\x00", BreakInMode.OFF),
            (b"\x01", BreakInMode.SEMI),
            (b"\x02", BreakInMode.FULL),
        ],
    )
    async def test_icom_get_break_in_returns_enum(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        payload: bytes,
        expected: BreakInMode,
    ) -> None:
        mock_transport.queue_response(_function_response(0x47, payload))
        result = await radio.get_break_in()
        assert result == expected
        assert isinstance(result, BreakInMode)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "expected_byte"),
        [
            (BreakInMode.OFF, 0x00),
            (BreakInMode.SEMI, 0x01),
            (BreakInMode.FULL, 0x02),
            (0, 0x00),
            (1, 0x01),
            (2, 0x02),
        ],
    )
    async def test_icom_set_break_in_accepts_int_or_enum(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        value: BreakInMode | int,
        expected_byte: int,
    ) -> None:
        await radio.set_break_in(value)
        assert mock_transport.sent_packets[-1].endswith(
            bytes([0x16, 0x47, expected_byte, 0xFD])
        )


class TestToneTsqlParity:
    """Test high-level tone/TSQL parity methods (#134)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "sub", "payload", "receiver", "expected"),
        [
            ("get_repeater_tone", 0x42, b"\x01", 0, True),
            ("get_repeater_tone", 0x42, b"\x00", 1, False),
            ("get_repeater_tsql", 0x43, b"\x01", 0, True),
            ("get_repeater_tsql", 0x43, b"\x00", 1, False),
        ],
    )
    async def test_get_repeater_toggle(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        sub: int,
        payload: bytes,
        receiver: int,
        expected: bool,
    ) -> None:
        mock_transport.queue_response(
            _function_response(sub, payload, receiver=receiver)
        )
        result = await getattr(radio, method_name)(receiver=receiver)
        assert result is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "value", "kwargs", "expected_tail"),
        [
            ("set_repeater_tone", True, {"receiver": 0}, b"\x29\x00\x16\x42\x01\xfd"),
            ("set_repeater_tone", False, {"receiver": 1}, b"\x29\x01\x16\x42\x00\xfd"),
            ("set_repeater_tsql", True, {"receiver": 0}, b"\x29\x00\x16\x43\x01\xfd"),
            ("set_repeater_tsql", False, {"receiver": 1}, b"\x29\x01\x16\x43\x00\xfd"),
        ],
    )
    async def test_set_repeater_toggle(
        self,
        radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        value: bool,
        kwargs: dict[str, int],
        expected_tail: bytes,
    ) -> None:
        await getattr(radio, method_name)(value, **kwargs)
        assert mock_transport.sent_packets[-1].endswith(expected_tail)

    @pytest.mark.asyncio
    async def test_get_tone_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x1B,
            sub=0x00,
            data=bytes([0x00, 0x88, 0x05]),
            receiver=0,
        )
        mock_transport.queue_response(_wrap_civ_in_udp(civ))
        assert await radio.get_tone_freq() == pytest.approx(88.5)

    @pytest.mark.asyncio
    async def test_get_tsql_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x1B,
            sub=0x01,
            data=bytes([0x01, 0x10, 0x09]),
            receiver=0,
        )
        mock_transport.queue_response(_wrap_civ_in_udp(civ))
        assert await radio.get_tsql_freq() == pytest.approx(110.9)

    @pytest.mark.asyncio
    async def test_set_tone_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_tone_freq(88.5)
        # cmd29 + receiver=0 + 0x1B + 0x00 + BCD(88.5) + FD
        assert mock_transport.sent_packets[-1].endswith(
            b"\x29\x00\x1b\x00\x00\x88\x05\xfd"
        )

    @pytest.mark.asyncio
    async def test_set_tsql_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_tsql_freq(110.9, receiver=1)
        assert mock_transport.sent_packets[-1].endswith(
            b"\x29\x01\x1b\x01\x01\x10\x09\xfd"
        )


class TestToneTsqlDualRxCmd29Guard:
    """MOR-1528: tone/TSQL methods must reject SUB routing without cmd29.

    IC-9700 is dual-RX (``receiver_count=2``) but declares no cmd29 routes
    (``[cmd29] routes = []``). Before this fix, ``receiver=1`` calls on the
    four tone/TSQL method pairs built a direct, receiver-unaware CI-V frame
    instead of raising — silently writing/reading MAIN while the caller
    believed it was targeting SUB. Sibling guarded commands (e.g.
    ``set_rf_gain``) already raise via ``_require_cmd29_route``; these
    methods now match that contract. ``receiver=0`` (MAIN) and IC-7610 (has
    cmd29 routes) behavior must stay byte-identical — see
    ``TestToneTsqlParity`` above, which exercises the IC-7610 fixture on
    both receivers and stays green.
    """

    @pytest.fixture
    def ic9700_radio(self, mock_transport: MockTransport):
        r = IcomRadio("192.168.1.102", timeout=0.05, model="IC-9700")
        r._civ_transport = mock_transport
        r._ctrl_transport = mock_transport
        r._connected = True
        yield r
        r._connected = False  # reset _conn_state so __del__ stays quiet

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "args"),
        [
            ("get_repeater_tone", ()),
            ("set_repeater_tone", (True,)),
            ("get_repeater_tsql", ()),
            ("set_repeater_tsql", (True,)),
            ("get_tone_freq", ()),
            ("set_tone_freq", (88.5,)),
            ("get_tsql_freq", ()),
            ("set_tsql_freq", (110.9,)),
        ],
    )
    async def test_sub_receiver_raises_without_cmd29_route(
        self,
        ic9700_radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        args: tuple,
    ) -> None:
        method = getattr(ic9700_radio, method_name)
        with pytest.raises(CommandError, match="no cmd29 route"):
            await method(*args, receiver=1)
        # No wire write occurred — the guard must fire before any send.
        assert mock_transport.sent_packets == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "args", "expected_tail"),
        [
            ("set_repeater_tone", (True,), b"\x16\x42\x01\xfd"),
            ("set_repeater_tsql", (True,), b"\x16\x43\x01\xfd"),
            ("set_tone_freq", (88.5,), b"\x1b\x00\x00\x88\x05\xfd"),
            ("set_tsql_freq", (110.9,), b"\x1b\x01\x01\x10\x09\xfd"),
        ],
    )
    async def test_main_receiver_still_sends_direct_frame(
        self,
        ic9700_radio: IcomRadio,
        mock_transport: MockTransport,
        method_name: str,
        args: tuple,
        expected_tail: bytes,
    ) -> None:
        method = getattr(ic9700_radio, method_name)
        await method(*args, receiver=0)
        assert mock_transport.sent_packets[-1].endswith(expected_tail)

    @pytest.mark.asyncio
    async def test_main_receiver_get_repeater_tone_still_works(
        self, ic9700_radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        civ = build_civ_frame(CONTROLLER_ADDR, 0xA2, 0x16, sub=0x42, data=b"\x01")
        mock_transport.queue_response(_wrap_civ_in_udp(civ))
        assert await ic9700_radio.get_repeater_tone(receiver=0) is True
        assert mock_transport.sent_packets[-1].endswith(b"\x16\x42\xfd")


class TestCodecProfileOverride:
    """Per-profile codec_preference overrides the global default (#797)."""

    def test_single_rx_profile_downgrades_to_mono_on_default(self) -> None:
        """IC-7300 profile pins mono → default audio_codec lands on mono."""
        radio = IcomRadio("192.168.1.100", model="IC-7300")
        assert radio._audio_codec == AudioCodec.PCM_1CH_16BIT

    def test_ic705_profile_downgrades_to_mono_on_default(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-705")
        assert radio._audio_codec == AudioCodec.PCM_1CH_16BIT

    def test_ic9700_profile_downgrades_to_mono_on_default(self) -> None:
        radio = IcomRadio("192.168.1.100", model="IC-9700")
        assert radio._audio_codec == AudioCodec.PCM_1CH_16BIT

    def test_ic7610_keeps_global_stereo_default(self) -> None:
        """IC-7610 has no codec_preference pin → global default (stereo)."""
        radio = IcomRadio("192.168.1.100", model="IC-7610")
        assert radio._audio_codec == AudioCodec.PCM_2CH_16BIT

    def test_explicit_audio_codec_wins_over_profile_preference(self) -> None:
        """Caller's explicit non-default choice overrides the profile pin."""
        radio = IcomRadio(
            "192.168.1.100",
            model="IC-7300",
            audio_codec=AudioCodec.ULAW_2CH,
        )
        assert radio._audio_codec == AudioCodec.ULAW_2CH

    def test_explicit_default_value_is_still_overridden(self) -> None:
        """Passing the global-default value explicitly = accepting the default.

        Callers who want to force stereo on a mono-pinned radio must pick a
        different codec (stereo or otherwise).  Explicit ``PCM_2CH_16BIT`` is
        indistinguishable from the unspecified default, so the profile pin wins.
        This is a documented trade-off — see ``_resolve_profile_codec``.
        """
        radio = IcomRadio(
            "192.168.1.100",
            model="IC-7300",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
        )
        assert radio._audio_codec == AudioCodec.PCM_1CH_16BIT
