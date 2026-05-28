"""Tests for CI-V 0x25/0x26 selected/unselected freq & mode commands."""

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    _CMD_SELECTED_FREQ,
    _CMD_SELECTED_MODE,
    build_civ_frame,
    get_selected_freq,
    get_unselected_freq,
    get_selected_mode,
    get_unselected_mode,
    parse_selected_freq_response,
    parse_selected_mode_response,
    set_selected_mode,
)
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame, Mode, bcd_encode

from test_radio import MockTransport, _wrap_civ_in_udp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _selected_freq_response(receiver: int, freq_hz: int) -> bytes:
    """Build a CI-V 0x25 frequency response wrapped in UDP."""
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_SELECTED_FREQ,
        data=bytes([receiver]) + bcd_encode(freq_hz),
    )
    return _wrap_civ_in_udp(civ)


def _selected_mode_response(
    receiver: int, mode: Mode, data_mode: int = 0, filt: int = 1
) -> bytes:
    """Build a CI-V 0x26 mode response wrapped in UDP."""
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_SELECTED_MODE,
        data=bytes([receiver, mode, data_mode, filt]),
    )
    return _wrap_civ_in_udp(civ)


# ---------------------------------------------------------------------------
# Command builder tests
# ---------------------------------------------------------------------------


class TestCommandBuilders:
    def test_get_selected_freq_frame(self) -> None:
        frame = get_selected_freq(to_addr=0x98)
        assert b"\x25\x00" in frame
        assert frame.startswith(b"\xfe\xfe")
        assert frame.endswith(b"\xfd")

    def test_get_unselected_freq_frame(self) -> None:
        frame = get_unselected_freq(to_addr=0x98)
        assert b"\x25\x01" in frame

    def test_get_selected_mode_frame(self) -> None:
        frame = get_selected_mode(to_addr=0x98)
        assert b"\x26\x00" in frame

    def test_get_unselected_mode_frame(self) -> None:
        frame = get_unselected_mode(to_addr=0x98)
        assert b"\x26\x01" in frame

    def test_set_selected_mode_frame(self) -> None:
        # X6200 CI-V address is 0xA4; controller 0xE0; Mode.LSB == 0x00.
        # Frame: FE FE A4 E0 26 00 <receiver=00> <mode=LSB=00> <data_mode=00>
        #        <filter=01> FD.
        frame = set_selected_mode(Mode.LSB, 0, 1, to_addr=0xA4)
        assert frame == b"\xfe\xfe\xa4\xe0\x26\x00\x00\x00\x01\xfd"


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParsers:
    def test_parse_selected_freq_response_main(self) -> None:
        freq_hz = 14_074_000
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x25,
            sub=None,
            data=bytes([0x00]) + bcd_encode(freq_hz),
        )
        rcvr, freq = parse_selected_freq_response(frame)
        assert rcvr == 0x00
        assert freq == freq_hz

    def test_parse_selected_freq_response_sub(self) -> None:
        freq_hz = 7_074_000
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x25,
            sub=None,
            data=bytes([0x01]) + bcd_encode(freq_hz),
        )
        rcvr, freq = parse_selected_freq_response(frame)
        assert rcvr == 0x01
        assert freq == freq_hz

    def test_parse_selected_freq_wrong_command(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x03,
            sub=None,
            data=bcd_encode(14_074_000),
        )
        with pytest.raises(ValueError, match="Not a 0x25 response"):
            parse_selected_freq_response(frame)

    def test_parse_selected_freq_too_short(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x25,
            sub=None,
            data=bytes([0x00, 0x01]),
        )
        with pytest.raises(ValueError, match="too short"):
            parse_selected_freq_response(frame)

    def test_parse_selected_mode_response_full(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x26,
            sub=None,
            data=bytes([0x00, Mode.USB, 0x01, 0x02]),
        )
        rcvr, mode, data_mode, filt = parse_selected_mode_response(frame)
        assert rcvr == 0x00
        assert mode == Mode.USB
        assert data_mode == 0x01
        assert filt == 0x02

    def test_parse_selected_mode_response_minimal(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x26,
            sub=None,
            data=bytes([0x01, Mode.CW]),
        )
        rcvr, mode, data_mode, filt = parse_selected_mode_response(frame)
        assert rcvr == 0x01
        assert mode == Mode.CW
        assert data_mode is None
        assert filt is None

    def test_parse_selected_mode_wrong_command(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x04,
            sub=None,
            data=bytes([Mode.USB]),
        )
        with pytest.raises(ValueError, match="Not a 0x26 response"):
            parse_selected_mode_response(frame)

    def test_parse_selected_mode_too_short(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x26,
            sub=None,
            data=bytes([0x00]),
        )
        with pytest.raises(ValueError, match="too short"):
            parse_selected_mode_response(frame)


# ---------------------------------------------------------------------------
# Radio integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(mock_transport: MockTransport):
    r = IcomRadio("192.168.1.100", timeout=0.05)
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    yield r
    r._connected = False  # reset _conn_state so __del__ stays quiet


class TestRadioSelectedFreq:
    @pytest.mark.asyncio
    async def test_get_selected_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_selected_freq_response(0x00, 14_074_000))
        freq = await radio._get_selected_freq()
        assert freq == 14_074_000

    @pytest.mark.asyncio
    async def test_get_unselected_freq(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_selected_freq_response(0x01, 7_074_000))
        freq = await radio._get_unselected_freq()
        assert freq == 7_074_000

    @pytest.mark.asyncio
    async def test_get_freq_receiver_sub_uses_cmd25(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """get_freq(receiver=1) should use 0x25 0x01 instead of VFO swap."""
        mock_transport.queue_response(_selected_freq_response(0x01, 7_074_000))
        freq = await radio.get_freq(receiver=1)
        assert freq == 7_074_000


class TestRadioSelectedMode:
    @pytest.mark.asyncio
    async def test_get_selected_mode(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_selected_mode_response(0x00, Mode.USB, filt=1))
        mode, filt = await radio._get_selected_mode()
        assert mode == Mode.USB
        assert filt == 1

    @pytest.mark.asyncio
    async def test_get_unselected_mode(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        mock_transport.queue_response(_selected_mode_response(0x01, Mode.LSB, filt=2))
        mode, filt = await radio._get_unselected_mode()
        assert mode == Mode.LSB
        assert filt == 2

    @pytest.mark.asyncio
    async def test_get_mode_receiver_sub_uses_cmd26(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """get_mode(receiver=1) should use 0x26 0x01 instead of VFO swap."""
        mock_transport.queue_response(_selected_mode_response(0x01, Mode.CW, filt=3))
        mode_name, filt = await radio.get_mode(receiver=1)
        assert mode_name == "CW"
        assert filt == 3


# ---------------------------------------------------------------------------
# Issue #715 — per-receiver VFO A/B state in poller
# ---------------------------------------------------------------------------


class TestVfoSlotOverrideCivRx:
    """CivRuntime routes 0x03/0x04 responses to vfo_a or vfo_b when the
    poller has installed a ``_vfo_slot_override`` entry for the target
    receiver."""

    def test_cmd03_routes_to_vfo_b_when_override_set(self, radio: IcomRadio) -> None:
        from rigplane.radio_state import RadioState
        from rigplane.types import bcd_encode

        radio._radio_state = RadioState()
        rs = radio._radio_state
        rs.main.active_slot = "A"
        rs.main.vfo_a = rs.main.vfo_a.__class__(freq_hz=14_000_000, mode="USB")
        radio._vfo_slot_override = {"MAIN": "B"}
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x03,
            sub=None,
            data=bcd_encode(21_000_000),
        )
        radio._civ_runtime._update_radio_state_from_frame(frame)
        assert rs.main.vfo_a.freq_hz == 14_000_000  # active slot unchanged
        assert rs.main.vfo_b.freq_hz == 21_000_000  # unselected slot populated

    def test_cmd04_routes_to_vfo_b_when_override_set(self, radio: IcomRadio) -> None:
        from rigplane.radio_state import RadioState

        radio._radio_state = RadioState()
        rs = radio._radio_state
        rs.main.active_slot = "A"
        radio._vfo_slot_override = {"MAIN": "B"}
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x04,
            sub=None,
            data=bytes([Mode.LSB, 2]),
        )
        radio._civ_runtime._update_radio_state_from_frame(frame)
        assert rs.main.vfo_a.mode == "USB"  # default, unchanged
        assert rs.main.vfo_b.mode == "LSB"
        assert rs.main.vfo_b.filter_num == 2

    def test_override_absent_falls_back_to_active_slot(self, radio: IcomRadio) -> None:
        """Without the override flag, 0x03 writes to the active slot as before."""
        from rigplane.radio_state import RadioState
        from rigplane.types import bcd_encode

        radio._radio_state = RadioState()
        rs = radio._radio_state
        rs.main.active_slot = "A"
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x03,
            sub=None,
            data=bcd_encode(14_074_000),
        )
        radio._civ_runtime._update_radio_state_from_frame(frame)
        assert rs.main.vfo_a.freq_hz == 14_074_000
        assert rs.main.vfo_b.freq_hz == 0

    def test_override_targets_sub_receiver(self, radio: IcomRadio) -> None:
        """Override for SUB routes cmd29-wrapped responses to sub.vfo_b."""
        from rigplane.radio_state import RadioState
        from rigplane.types import bcd_encode

        radio._radio_state = RadioState()
        rs = radio._radio_state
        rs.sub.active_slot = "A"
        radio._vfo_slot_override = {"SUB": "B"}
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x03,
            sub=None,
            data=bcd_encode(7_074_000),
            receiver=0x01,  # cmd29-wrapped response from SUB
        )
        radio._civ_runtime._update_radio_state_from_frame(frame)
        assert rs.sub.vfo_a.freq_hz == 0
        assert rs.sub.vfo_b.freq_hz == 7_074_000


class TestPollerUnselectedSlotGate:
    """Poller gates the unselected-slot read behind PTT, queue pressure,
    recent user writes, and a per-receiver rate limit."""

    def _make_poller(self, model: str, active: str = "MAIN"):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock
        from rigplane.profiles import resolve_radio_profile
        from rigplane.radio_state import RadioState
        from rigplane.rigctld.state_cache import StateCache
        from rigplane.web.radio_poller import CommandQueue, RadioPoller

        profile = resolve_radio_profile(model=model)
        radio = MagicMock()
        radio.profile = profile
        radio.model = profile.model
        radio.capabilities = set(profile.capabilities)
        radio._radio_state = SimpleNamespace(active=active)
        radio.send_civ = AsyncMock()
        radio.set_freq = AsyncMock()
        radio.set_mode = AsyncMock()
        state = RadioState()
        poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)
        return poller, radio, state

    @pytest.mark.asyncio
    async def test_ic7610_skips_unselected_slot_poll_without_swap_ab(self) -> None:
        """IC-7610 declares only swap_main_sub (0x07 0xB0 toggles MAIN↔SUB,
        NOT A/B within a receiver). Without a true swap_ab_code, using the
        MAIN↔SUB byte as a fallback would flip the radio's active-RX state
        every slow-poll cycle — the bug reported against #743/#715. The gate
        must skip the cycle entirely on such profiles.
        """
        poller, radio, state = self._make_poller("IC-7610")
        await poller._poll_unselected_slot(0)
        await poller._poll_unselected_slot(1)
        # Gate rejects both: no CI-V traffic, no MAIN↔SUB flip.
        assert radio.send_civ.await_count == 0

    @pytest.mark.asyncio
    async def test_ic7300_single_receiver_polls_vfo_b(self) -> None:
        poller, radio, state = self._make_poller("IC-7300")
        await poller._poll_unselected_slot(0)
        # swap + 0x03 + 0x04 + swap-back = 4 sends
        assert radio.send_civ.await_count == 4

    @pytest.mark.asyncio
    async def test_gate_skips_when_ptt_active(self) -> None:
        poller, radio, state = self._make_poller("IC-7610")
        state.ptt = True
        await poller._poll_unselected_slot(0)
        radio.send_civ.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_skips_when_queue_has_commands(self) -> None:
        from rigplane.web.radio_poller import PttOn

        poller, radio, state = self._make_poller("IC-7610")
        poller._queue.put(PttOn())
        await poller._poll_unselected_slot(0)
        radio.send_civ.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_skips_after_recent_user_write(self) -> None:
        import time as _time

        poller, radio, state = self._make_poller("IC-7610")
        poller._last_user_write_ts = _time.monotonic()
        await poller._poll_unselected_slot(0)
        radio.send_civ.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rate_limits_per_receiver(self) -> None:
        poller, radio, state = self._make_poller("IC-7610")
        await poller._poll_unselected_slot(0)
        first = radio.send_civ.await_count
        # Immediate re-poll should be gated by the per-receiver interval.
        await poller._poll_unselected_slot(0)
        assert radio.send_civ.await_count == first

    @pytest.mark.asyncio
    async def test_set_freq_updates_last_user_write_ts(self) -> None:
        from rigplane.web.radio_poller import SetFreq

        poller, radio, state = self._make_poller("IC-7610")
        assert poller._last_user_write_ts == 0.0
        await poller._execute(SetFreq(14_074_000, receiver=0))
        assert poller._last_user_write_ts > 0.0
