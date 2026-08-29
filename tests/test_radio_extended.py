"""Extended tests for IcomRadio — VFO, split, attenuator, preamp, CW, power control, audio, disconnected states."""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commander import Priority
from rigplane.exceptions import CommandError, ConnectionError, TimeoutError
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame, Mode, bcd_encode

# Reuse helpers from test_radio
from test_radio import (
    MockTransport,
    _ack_response,
    _nak_response,
    _wrap_civ_in_udp,
)


def _digisel_off_response() -> bytes:
    # Radio responds: FE FE E0 98 29 00 16 4E 00 FD
    civ = bytes.fromhex("fefee0982900164e00fd")
    return _wrap_civ_in_udp(civ)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(mock_transport: MockTransport) -> IcomRadio:
    r = IcomRadio("192.168.1.100", timeout=0.05)
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    return r


# ---------------------------------------------------------------------------
# VFO tests
# ---------------------------------------------------------------------------


class TestVFO:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model", "vfo", "expected"),
        [
            ("IC-7300", "A", 0x00),
            ("IC-7300", "B", 0x01),
            ("IC-7300", "MAIN", 0x00),
            ("IC-7300", "SUB", 0x01),
            ("IC-7610", "A", 0xD0),
            ("IC-7610", "B", 0xD1),
            ("IC-7610", "MAIN", 0xD0),
            ("IC-7610", "SUB", 0xD1),
            # The lowercase row must name a *secondary* VFO. "main" would
            # resolve to vfo_main_code with or without the method's
            # ``.upper()`` -- it falls to the same else-branch either way --
            # so it could not fail if that call were dropped. "sub" can.
            ("IC-7610", "sub", 0xD1),
        ],
    )
    async def test_set_vfo_wire_sends_the_profile_code(
        self,
        mock_transport: MockTransport,
        model: str,
        vfo: str,
        expected: int,
    ) -> None:
        """The selector byte is the profile's, not a table in the method.

        Both spellings of a receiver resolve to the one pair the profile
        declares, so an ``ab`` profile never emits 0xD0/0xD1 and a
        ``main_sub`` profile never emits 0x00/0x01.  The table this
        replaced went the other way — it answered by name alone, so
        ``"MAIN"`` sent 0xD0 to an IC-7300 that has no MAIN.
        """
        r = IcomRadio("192.168.1.100", timeout=0.05, model=model)
        r._civ_transport = mock_transport
        r._ctrl_transport = mock_transport
        r._connected = True
        mock_transport.queue_response(
            _wrap_civ_in_udp(build_civ_frame(CONTROLLER_ADDR, r._radio_addr, 0xFB))
        )
        try:
            await r._set_vfo_wire(vfo)
        finally:
            r._connected = False
        # Parse the frame out; do not search the datagram for the two bytes.
        # The UDP control header ends ``... 00 07 00 00 00`` on every packet
        # this transport sends, so ``b"\x07\x00" in packet`` is true whatever
        # the payload — it would pass all four IC-7300 rows unchanged.
        sent = mock_transport.sent_packets[-1]
        frame = parse_civ_frame(sent[sent.index(b"\xfe\xfe") :])
        assert frame.command == 0x07
        assert frame.data == bytes([expected])

    @pytest.mark.asyncio
    async def test_set_vfo_wire_without_a_profile_code_raises(
        self, mock_transport: MockTransport
    ) -> None:
        """No declared code means no frame — never a byte invented here.

        X6100 is an ``ab`` profile whose ``[vfo]`` section declares neither
        ``main_select`` nor ``sub_select``.  The deleted table answered
        ``"MAIN"`` with 0xD0 regardless, so a single-receiver rig got a
        dual-receiver MAIN-select opcode.  That is the failure this
        replaces; the table's 0x00 default was for names outside its four
        keys, which is a different hole.
        """
        r = IcomRadio("192.168.1.100", timeout=0.05, model="X6100")
        r._civ_transport = mock_transport
        r._ctrl_transport = mock_transport
        r._connected = True
        try:
            with pytest.raises(CommandError, match="no VFO select code"):
                await r._set_vfo_wire("MAIN")
        finally:
            r._connected = False
        assert mock_transport.sent_packets == []

    @pytest.mark.asyncio
    async def test_set_vfo_wire_nak(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_nak_response())
        with pytest.raises(CommandError):
            await radio._set_vfo_wire("A")

    @pytest.mark.asyncio
    async def test_set_vfo_wire_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r._set_vfo_wire("A")

    @pytest.mark.asyncio
    async def test_set_vfo_wire_does_not_warn(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Internal ``_set_vfo_wire`` is silent — used by capability impls."""
        import warnings

        mock_transport.queue_response(_ack_response())
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            await radio._set_vfo_wire("A")

    def test_set_vfo_attribute_removed(self) -> None:
        """Deprecated ``set_vfo`` overload is removed in v0.20 (#1206)."""
        r = IcomRadio("192.168.1.100")
        assert not hasattr(r, "set_vfo")

    def test_select_vfo_alias_removed(self) -> None:
        """Deprecated ``select_vfo`` alias is removed in v0.20 (#1206)."""
        r = IcomRadio("192.168.1.100")
        assert not hasattr(r, "select_vfo")

    def test_vfo_exchange_attribute_removed(self) -> None:
        """Deprecated ``vfo_exchange`` alias is removed in v0.19."""
        r = IcomRadio("192.168.1.100")
        assert not hasattr(r, "vfo_exchange")

    def test_vfo_equalize_attribute_removed(self) -> None:
        """Deprecated ``vfo_equalize`` alias is removed in v0.19."""
        r = IcomRadio("192.168.1.100")
        assert not hasattr(r, "vfo_equalize")


# ---------------------------------------------------------------------------
# Split mode
# ---------------------------------------------------------------------------


class TestSplitMode:
    @pytest.mark.asyncio
    async def test_split_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_ack_response())
        await radio.set_split(True)

    @pytest.mark.asyncio
    async def test_split_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_ack_response())
        await radio.set_split(False)

    @pytest.mark.asyncio
    async def test_split_nak(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_nak_response())
        with pytest.raises(CommandError):
            await radio.set_split(True)

    @pytest.mark.asyncio
    async def test_split_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.set_split(True)

    @pytest.mark.asyncio
    async def test_get_split_round_trip(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """``set_split(True)`` followed by ``get_split()`` returns True."""
        # Set: ACK
        mock_transport.queue_response(_ack_response())
        await radio.set_split(True)
        # Get: radio responds with cmd 0x0F + data byte 0x01
        civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x0F, data=b"\x01")
        mock_transport.queue_response(_wrap_civ_in_udp(civ))
        assert await radio.get_split() is True

    @pytest.mark.asyncio
    async def test_get_split_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x0F, data=b"\x00")
        mock_transport.queue_response(_wrap_civ_in_udp(civ))
        assert await radio.get_split() is False

    @pytest.mark.asyncio
    async def test_get_split_falls_back_to_cache_on_no_response(
        self, radio: IcomRadio
    ) -> None:
        """Issue #1143: ``get_split`` honors documented cache fallback when
        ``_send_civ_expect`` raises ``CommandError`` (e.g. no response on
        transient link issues / unsupported reads).
        """
        radio._last_split = True
        radio._send_civ_expect = AsyncMock(  # type: ignore[method-assign]
            side_effect=CommandError("No response for get_split")
        )
        assert await radio.get_split() is True

    @pytest.mark.asyncio
    async def test_get_split_no_response_no_cache_returns_false(
        self, radio: IcomRadio
    ) -> None:
        """When there is no cached value and no response, return ``False``
        instead of propagating ``CommandError``."""
        radio._last_split = None
        radio._send_civ_expect = AsyncMock(  # type: ignore[method-assign]
            side_effect=CommandError("No response for get_split")
        )
        assert await radio.get_split() is False

    @pytest.mark.asyncio
    async def test_get_split_falls_back_to_cache_on_timeout(
        self, radio: IcomRadio
    ) -> None:
        """Issue #1158: ``get_split`` must also fall back to cache when
        ``_send_civ_expect`` raises ``TimeoutError`` — that is the actual
        exception the CI-V runtime raises on real radio silence (see
        ``_civ_rx._execute_civ_raw``).
        """
        radio._last_split = True
        radio._send_civ_expect = AsyncMock(  # type: ignore[method-assign]
            side_effect=TimeoutError("CI-V response timed out")
        )
        assert await radio.get_split() is True

    @pytest.mark.asyncio
    async def test_get_split_timeout_no_cache_returns_false(
        self, radio: IcomRadio
    ) -> None:
        """Issue #1158: when there is no cached value and the radio times out,
        ``get_split`` should return ``False`` instead of propagating
        ``TimeoutError``.
        """
        radio._last_split = None
        radio._send_civ_expect = AsyncMock(  # type: ignore[method-assign]
            side_effect=TimeoutError("CI-V response timed out")
        )
        assert await radio.get_split() is False


# ---------------------------------------------------------------------------
# Attenuator
# ---------------------------------------------------------------------------


class TestAttenuator:
    @pytest.mark.asyncio
    async def test_att_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_attenuator(True)
        assert radio._attenuator_state is True

    @pytest.mark.asyncio
    async def test_att_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        await radio.set_attenuator(False)
        assert radio._attenuator_state is False

    @pytest.mark.asyncio
    async def test_att_no_response_needed(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_attenuator is fire-and-forget — completes without a radio response."""
        await radio.set_attenuator(True)  # must not raise

    @pytest.mark.asyncio
    async def test_att_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.set_attenuator(True)


# ---------------------------------------------------------------------------
# Preamp
# ---------------------------------------------------------------------------


class TestPreamp:
    @pytest.mark.asyncio
    async def test_preamp_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(
            _digisel_off_response()
        )  # pre-flight DIGI-SEL check (send #1)
        # set_preamp is fire-and-forget — no ACK needed
        await radio.set_preamp(1)
        assert radio._preamp_level == 1

    @pytest.mark.asyncio
    async def test_preamp_level2(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(
            _digisel_off_response()
        )  # pre-flight DIGI-SEL check (send #1)
        # set_preamp is fire-and-forget — no ACK needed
        await radio.set_preamp(2)
        assert radio._preamp_level == 2

    @pytest.mark.asyncio
    async def test_preamp_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        # level=0 skips DIGI-SEL check; fire-and-forget, no response needed
        await radio.set_preamp(0)
        assert radio._preamp_level == 0

    @pytest.mark.asyncio
    async def test_preamp_no_response_needed(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_preamp is fire-and-forget — completes without a radio response."""
        mock_transport.queue_response(
            _digisel_off_response()
        )  # pre-flight DIGI-SEL check still awaits response
        await radio.set_preamp(1)  # must not raise

    @pytest.mark.asyncio
    async def test_preamp_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.set_preamp(1)


# ---------------------------------------------------------------------------
# CW keying
# ---------------------------------------------------------------------------


class TestCW:
    @pytest.mark.asyncio
    async def test_send_cw_text(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_ack_response())
        await radio.send_cw_text("CQ")
        assert len(mock_transport.sent_packets) > 0

    @pytest.mark.asyncio
    async def test_send_cw_long_text(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Text longer than 30 chars should produce multiple frames."""
        text = "A" * 60  # 2 chunks
        mock_transport.queue_response(_ack_response())  # ACK for chunk #1 (send #1)
        mock_transport.queue_response_on_send(
            2, _ack_response()
        )  # ACK for chunk #2 (send #2)
        await radio.send_cw_text(text)
        assert len(mock_transport.sent_packets) == 2

    @pytest.mark.asyncio
    async def test_send_cw_nak(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_nak_response())
        with pytest.raises(CommandError):
            await radio.send_cw_text("CQ")

    @pytest.mark.asyncio
    async def test_stop_cw(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_ack_response())
        await radio.stop_cw_text()

    @pytest.mark.asyncio
    async def test_cw_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.send_cw_text("CQ")


# ---------------------------------------------------------------------------
# Receiver-aware contract / fallback routing
# ---------------------------------------------------------------------------


class TestReceiverAwareContract:
    @pytest.mark.asyncio
    async def test_get_frequency_receiver_sub_uses_cmd25(
        self, radio: IcomRadio
    ) -> None:
        expected = 7_074_000
        radio._send_civ_raw = AsyncMock(  # type: ignore[method-assign]
            return_value=CivFrame(
                to_addr=CONTROLLER_ADDR,
                from_addr=IC_7610_ADDR,
                command=0x25,
                sub=None,
                data=bytes([0x01]) + bcd_encode(expected),
            )
        )

        got = await radio.get_freq(receiver=1)

        assert got == expected

    @pytest.mark.asyncio
    async def test_get_mode_receiver_sub_uses_cmd26(self, radio: IcomRadio) -> None:
        radio._send_civ_raw = AsyncMock(  # type: ignore[method-assign]
            return_value=CivFrame(
                to_addr=CONTROLLER_ADDR,
                from_addr=IC_7610_ADDR,
                command=0x26,
                sub=None,
                data=bytes([0x01, Mode.LSB, 0x00, 2]),
            )
        )

        mode_name, filt = await radio.get_mode(receiver=1)

        assert mode_name == "LSB"
        assert filt == 2

    @pytest.mark.asyncio
    async def test_set_frequency_receiver_sub_uses_vfo_fallback(
        self, radio: IcomRadio
    ) -> None:
        # Issue #1172: receiver-VFO fallback now goes through the
        # internal ``_set_vfo_wire`` so the legacy ``set_vfo`` overload
        # does not self-trigger its DeprecationWarning.
        radio._set_vfo_wire = AsyncMock()  # type: ignore[method-assign]
        radio._send_civ_raw = AsyncMock(return_value=None)  # type: ignore[method-assign]

        await radio.set_freq(14_074_000, receiver=1)

        radio._set_vfo_wire.assert_has_awaits([call("SUB"), call("MAIN")])
        radio._send_civ_raw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_mode_receiver_sub_uses_vfo_fallback(
        self, radio: IcomRadio
    ) -> None:
        radio._set_vfo_wire = AsyncMock()  # type: ignore[method-assign]
        radio._send_civ_raw = AsyncMock(return_value=None)  # type: ignore[method-assign]

        await radio.set_mode("USB", receiver=1)

        radio._set_vfo_wire.assert_has_awaits([call("SUB"), call("MAIN")])
        radio._send_civ_raw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_cw_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.stop_cw_text()


# ---------------------------------------------------------------------------
# Power control
# ---------------------------------------------------------------------------


class TestPowerControl:
    @pytest.mark.asyncio
    async def test_power_on(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_ack_response())
        await radio.power_control(True)

    @pytest.mark.asyncio
    async def test_power_off(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_ack_response())
        await radio.power_control(False)

    @pytest.mark.asyncio
    async def test_power_on_nak_tolerated(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Power ON with NAK is tolerated (IC-7610 may NAK while booting)."""
        mock_transport.queue_response(_nak_response())
        # Should NOT raise — power-on NAK is logged but not fatal
        await radio.power_control(True)

    @pytest.mark.asyncio
    async def test_power_off_nak_raises(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """Power OFF with NAK should still raise."""
        mock_transport.queue_response(_nak_response())
        with pytest.raises(CommandError):
            await radio.power_control(False)

    @pytest.mark.asyncio
    async def test_power_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.power_control(True)


# ---------------------------------------------------------------------------
# Audio (disconnected / error paths)
# ---------------------------------------------------------------------------


class TestAudio:
    @pytest.mark.asyncio
    async def test_start_rx_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.start_audio_rx_opus(lambda pkt: None)

    @pytest.mark.asyncio
    async def test_start_tx_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.start_audio_tx_opus()

    @pytest.mark.asyncio
    async def test_push_tx_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.push_audio_tx_opus(b"\x00" * 100)

    @pytest.mark.asyncio
    async def test_push_tx_not_started(self, radio: IcomRadio) -> None:
        with pytest.raises(RuntimeError, match="Audio TX not started"):
            await radio.push_audio_tx_opus(b"\x00" * 100)

    @pytest.mark.asyncio
    async def test_start_tx_pcm_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.start_audio_tx_pcm()

    @pytest.mark.asyncio
    async def test_push_tx_pcm_not_started(self, radio: IcomRadio) -> None:
        with pytest.raises(RuntimeError, match="start_audio_tx_pcm"):
            await radio.push_audio_tx_pcm(b"\x00" * 1920)

    @pytest.mark.asyncio
    async def test_no_audio_port(self, radio: IcomRadio) -> None:
        radio._audio_port = 0
        with pytest.raises(ConnectionError, match="Audio port not available"):
            await radio.start_audio_rx_opus(lambda pkt: None)

    @pytest.mark.asyncio
    async def test_stop_rx_noop_when_no_stream(self, radio: IcomRadio) -> None:
        await radio.stop_audio_rx_opus()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_tx_noop_when_no_stream(self, radio: IcomRadio) -> None:
        await radio.stop_audio_tx_opus()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_tx_pcm_noop_when_no_stream(self, radio: IcomRadio) -> None:
        await radio.stop_audio_tx_pcm()  # should not raise

    def test_get_audio_stats_idle_without_stream(self, radio: IcomRadio) -> None:
        stats = radio.get_audio_stats()
        assert stats["active"] is False
        assert stats["state"] == "idle"
        assert stats["packet_loss_percent"] == 0.0

    def test_get_audio_stats_delegates_to_audio_stream(self, radio: IcomRadio) -> None:
        expected = {"active": True, "state": "receiving", "rx_packets_received": 5}
        radio._audio_stream = MagicMock()
        radio._audio_stream.get_audio_stats.return_value = expected

        assert radio.get_audio_stats() == expected


# ---------------------------------------------------------------------------
# PTT disconnected
# ---------------------------------------------------------------------------


class TestPttDisconnected:
    @pytest.mark.asyncio
    async def test_set_ptt_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.set_ptt(True)

    @pytest.mark.asyncio
    async def test_ptt_is_fire_and_forget(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """set_ptt is fire-and-forget: IMMEDIATE priority, no ACK wait."""
        send_mock = AsyncMock()
        radio._send_civ_raw = send_mock  # type: ignore[method-assign]

        await radio.set_ptt(True)

        send_mock.assert_awaited_once()
        _, kwargs = send_mock.await_args
        assert kwargs["priority"] == Priority.IMMEDIATE
        assert kwargs["wait_response"] is False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestInternals:
    def test_check_connected_raises(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            r._check_connected()

    def test_wrap_civ(self, radio: IcomRadio) -> None:
        civ = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x03)
        pkt = radio._civ_runtime._wrap_civ(civ)
        assert len(pkt) == 0x15 + len(civ)
        assert pkt[0x10] == 0xC1

    @pytest.mark.asyncio
    async def test_flush_queue_empty(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        count = await IcomRadio._flush_queue(mock_transport)
        assert count == 0

    @pytest.mark.asyncio
    async def test_flush_queue_with_data(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(b"\x00" * 16)
        mock_transport.queue_response(b"\x00" * 16)
        count = await IcomRadio._flush_queue(mock_transport)
        assert count == 2


# ---------------------------------------------------------------------------
# Constructor defaults
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_defaults(self) -> None:
        r = IcomRadio("10.0.0.1")
        assert r._host == "10.0.0.1"
        assert r._port == 50001
        assert r._radio_addr == IC_7610_ADDR
        assert r._timeout == 5.0

    def test_custom_params(self) -> None:
        r = IcomRadio(
            "10.0.0.2",
            port=50002,
            username="u",
            password="p",
            radio_addr=0x94,
            timeout=10.0,
        )
        assert r._host == "10.0.0.2"
        assert r._port == 50002
        assert r._username == "u"
        assert r._password == "p"
        assert r._radio_addr == 0x94
        assert r._timeout == 10.0

    def test_model_sets_profile_default_radio_addr_when_not_overridden(self) -> None:
        r = IcomRadio("10.0.0.3", model="IC-7300")
        assert r.model == "IC-7300"
        assert r._radio_addr == 0x94

    def test_explicit_radio_addr_override_wins_over_profile_default(self) -> None:
        r = IcomRadio("10.0.0.4", model="IC-7300", radio_addr=0x98)
        assert r.model == "IC-7300"
        assert r._radio_addr == 0x98
