"""Mock-based integration tests for operator toggle commands (issue #131, Part 3).

Tests the full request/response cycle for all 10 operator toggle commands using a
local mock radio server.  No real hardware required — runs in CI without env vars.

Commands under test:
  1. AGC Status
  2. Audio Peak Filter
  3. Auto Notch
  4. Compressor Status
  5. Monitor Status
  6. Vox Status
  7. Break-In Status
  8. Manual Notch
  9. Twin Peak Filter
  10. Dial Lock Status

Run with::

    pytest tests/integration/test_operator_toggles_integration.py -v
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

# Make tests/ importable from tests/integration/
sys.path.insert(0, str(Path(__file__).parent.parent))

# All tests in this module use MockIcomRadio and require no real hardware.
pytestmark = pytest.mark.mock_integration

from rigplane.commands import RECEIVER_MAIN, RECEIVER_SUB  # noqa: E402
from rigplane.radio import IcomRadio  # noqa: E402, TID251
from rigplane.types import AgcMode, AudioPeakFilter, BreakInMode  # noqa: E402
from _perf_helpers import fast_connect  # noqa: E402
from mock_server import MockIcomRadio  # noqa: E402

# ---------------------------------------------------------------------------
# Local CI-V constants (keep mock self-contained)
# ---------------------------------------------------------------------------

_CMD_PREAMP = 0x16
_CMD_CMD29 = 0x29
_CMD_ACK = 0xFB

_SUB_AGC = 0x12
_SUB_AUDIO_PEAK_FILTER = 0x32
_SUB_AUTO_NOTCH = 0x41
_SUB_COMPRESSOR = 0x44
_SUB_MONITOR = 0x45
_SUB_VOX = 0x46
_SUB_BREAK_IN = 0x47
_SUB_MANUAL_NOTCH = 0x48
_SUB_TWIN_PEAK_FILTER = 0x4F
_SUB_DIAL_LOCK = 0x50

_CONTROLLER_ADDR = 0xE0
_RADIO_ADDR = 0x98


def _bcd(v: int) -> int:
    """Encode 0-99 to one BCD byte."""
    return ((v // 10) << 4) | (v % 10)


def _build_civ(
    to: int,
    frm: int,
    cmd: int,
    sub: int | None = None,
    data: bytes = b"",
) -> bytes:
    """Build a minimal CI-V frame."""
    frame = bytearray(b"\xfe\xfe")
    frame.append(to)
    frame.append(frm)
    frame.append(cmd)
    if sub is not None:
        frame.append(sub)
    frame.extend(data)
    frame.append(0xFD)
    return bytes(frame)


def _build_cmd29_civ(
    to: int,
    frm: int,
    receiver: int,
    inner_cmd: int,
    sub: int,
    data: bytes = b"",
) -> bytes:
    """Build a Command29-wrapped CI-V frame."""
    return _build_civ(
        to,
        frm,
        _CMD_CMD29,
        data=bytes([receiver, inner_cmd, sub]) + data,
    )


# ---------------------------------------------------------------------------
# Extended mock with operator toggle state
# ---------------------------------------------------------------------------


class ToggleMockRadio(MockIcomRadio):
    """MockIcomRadio extended with operator toggle state.

    Handles all 10 operator toggle commands and supports unsolicited frame
    injection for testing push-from-radio state updates.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        # Global (non-receiver) toggle state
        self._agc: int = int(AgcMode.FAST)  # 1 = FAST
        self._compressor: int = 0
        self._monitor: int = 0
        self._vox: int = 0
        self._break_in: int = int(BreakInMode.OFF)  # 0 = OFF
        self._dial_lock: int = 0
        # Per-receiver toggle state
        self._apf: dict[int, int] = {0: 0, 1: 0}
        self._auto_notch: dict[int, int] = {0: 0, 1: 0}
        self._manual_notch: dict[int, int] = {0: 0, 1: 0}
        self._twin_peak: dict[int, int] = {0: 0, 1: 0}
        # Unsolicited injection tracking
        self._civ_client_addr: tuple[str, int] | None = None
        self._civ_client_proto: object = None

    # ------------------------------------------------------------------
    # Unsolicited frame injection
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        label: str,
        proto: object,
    ) -> None:
        if label == "civ":
            self._civ_client_addr = addr
            self._civ_client_proto = proto
        super()._dispatch(data, addr, ptype, sender_id, seq, label, proto)

    def inject_unsolicited_civ(self, civ_frame: bytes) -> None:
        """Send an unsolicited CI-V frame to the connected CIV client."""
        if self._civ_client_addr is None or self._civ_client_proto is None:
            return
        pkt = self._wrap_civ(civ_frame, self._civ_client_id)
        self._civ_client_proto.send(pkt, self._civ_client_addr)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # CI-V dispatch overrides
    # ------------------------------------------------------------------

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        """Handle direct 0x16 operator toggle commands in addition to base commands."""
        to = from_addr
        frm = self._radio_addr

        if cmd == _CMD_PREAMP:
            if not payload:
                return self._civ_nak(to, frm)
            return self._handle_direct_func(payload[0], payload[1:], to, frm)

        return super()._dispatch_civ(cmd, payload, from_addr)

    def _handle_direct_func(
        self, sub: int, rest: bytes, to: int, frm: int
    ) -> bytes | None:
        """Dispatch a direct 0x16 <sub> [value] command."""
        if sub == _SUB_AGC:
            if rest:
                raw = rest[0]
                self._agc = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                return self._civ_ack(to, frm)
            return self._civ_frame(
                to, frm, _CMD_PREAMP, sub=_SUB_AGC, data=bytes([_bcd(self._agc)])
            )

        if sub == _SUB_COMPRESSOR:
            if rest:
                self._compressor = rest[0]
                return self._civ_ack(to, frm)
            return self._civ_frame(
                to,
                frm,
                _CMD_PREAMP,
                sub=_SUB_COMPRESSOR,
                data=bytes([self._compressor]),
            )

        if sub == _SUB_MONITOR:
            if rest:
                self._monitor = rest[0]
                return self._civ_ack(to, frm)
            return self._civ_frame(
                to, frm, _CMD_PREAMP, sub=_SUB_MONITOR, data=bytes([self._monitor])
            )

        if sub == _SUB_VOX:
            if rest:
                self._vox = rest[0]
                return self._civ_ack(to, frm)
            return self._civ_frame(
                to, frm, _CMD_PREAMP, sub=_SUB_VOX, data=bytes([self._vox])
            )

        if sub == _SUB_BREAK_IN:
            if rest:
                raw = rest[0]
                self._break_in = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                return self._civ_ack(to, frm)
            return self._civ_frame(
                to,
                frm,
                _CMD_PREAMP,
                sub=_SUB_BREAK_IN,
                data=bytes([_bcd(self._break_in)]),
            )

        if sub == _SUB_DIAL_LOCK:
            if rest:
                self._dial_lock = rest[0]
                return self._civ_ack(to, frm)
            return self._civ_frame(
                to, frm, _CMD_PREAMP, sub=_SUB_DIAL_LOCK, data=bytes([self._dial_lock])
            )

        return self._civ_nak(to, frm)

    def _dispatch_cmd29(
        self, real_cmd: int, inner: bytes, from_addr: int, receiver: int
    ) -> bytes | None:
        """Handle Command29-wrapped operator-toggle commands."""
        to = from_addr
        frm = self._radio_addr

        if real_cmd == _CMD_PREAMP and inner:
            sub = inner[0]
            rest = inner[1:]

            if sub == _SUB_AGC:
                if not rest:
                    return self._civ_frame(
                        to,
                        frm,
                        _CMD_CMD29,
                        data=bytes([receiver, _CMD_PREAMP, _SUB_AGC, _bcd(self._agc)]),
                    )
                if len(rest) != 1:
                    return self._civ_nak(to, frm)
                raw = rest[0]
                value = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                if (
                    raw >> 4 > 9
                    or raw & 0x0F > 9
                    or value
                    not in (
                        AgcMode.FAST,
                        AgcMode.MID,
                        AgcMode.SLOW,
                    )
                ):
                    return self._civ_nak(to, frm)
                self._agc = value
                return self._civ_ack(to, frm)

            if sub == _SUB_AUDIO_PEAK_FILTER:
                if rest:
                    self._apf[receiver] = rest[0]
                    return self._civ_ack(to, frm)
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [
                            receiver,
                            _CMD_PREAMP,
                            _SUB_AUDIO_PEAK_FILTER,
                            self._apf.get(receiver, 0),
                        ]
                    ),
                )

            if sub == _SUB_AUTO_NOTCH:
                if rest:
                    self._auto_notch[receiver] = rest[0]
                    return self._civ_ack(to, frm)
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [
                            receiver,
                            _CMD_PREAMP,
                            _SUB_AUTO_NOTCH,
                            self._auto_notch.get(receiver, 0),
                        ]
                    ),
                )

            if sub == _SUB_MANUAL_NOTCH:
                if rest:
                    self._manual_notch[receiver] = rest[0]
                    return self._civ_ack(to, frm)
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [
                            receiver,
                            _CMD_PREAMP,
                            _SUB_MANUAL_NOTCH,
                            self._manual_notch.get(receiver, 0),
                        ]
                    ),
                )

            if sub == _SUB_TWIN_PEAK_FILTER:
                if rest:
                    self._twin_peak[receiver] = rest[0]
                    return self._civ_ack(to, frm)
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [
                            receiver,
                            _CMD_PREAMP,
                            _SUB_TWIN_PEAK_FILTER,
                            self._twin_peak.get(receiver, 0),
                        ]
                    ),
                )

        # Fall through to parent for ATT (0x11), PREAMP status (sub 0x02), DIGI-SEL (sub 0x4E)
        return super()._dispatch_cmd29(real_cmd, inner, from_addr, receiver)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def toggle_mock() -> AsyncGenerator[ToggleMockRadio, None]:
    """Start a ToggleMockRadio server for each test, stop it after."""
    server = ToggleMockRadio()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def toggle_radio(toggle_mock: ToggleMockRadio) -> AsyncGenerator[IcomRadio, None]:
    """IcomRadio connected to ToggleMockRadio, disconnected after test."""
    radio = IcomRadio(
        host="127.0.0.1",
        port=toggle_mock.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
    )
    with fast_connect():
        await radio.connect()
    yield radio
    await radio.disconnect()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_SETTLE = 0.05  # seconds: wait after fire-and-forget SET before GET


# ---------------------------------------------------------------------------
# 1. AGC Status
# ---------------------------------------------------------------------------


class TestAgcToggle:
    """AGC mode cycle: FAST → MID → SLOW."""

    async def test_agc_default_is_fast(self, toggle_radio: IcomRadio) -> None:
        mode = await toggle_radio.get_agc()
        assert mode == AgcMode.FAST

    async def test_agc_cycle_fast_mid_slow(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """AGC: FAST → MID → SLOW → FAST full cycle."""
        for target in (AgcMode.MID, AgcMode.SLOW, AgcMode.FAST):
            await toggle_radio.set_agc(target)
            await asyncio.sleep(_SETTLE)
            got = await toggle_radio.get_agc()
            assert got == target, f"AGC: expected {target}, got {got}"
            assert toggle_mock._agc == int(target)

    async def test_agc_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Radio sends unsolicited AGC change; _notify_change fires."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        # Radio pushes AGC=SLOW unsolicited (e.g., front-panel knob turn)
        frame = _build_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            _CMD_PREAMP,
            sub=_SUB_AGC,
            data=bytes([_bcd(int(AgcMode.SLOW))]),
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "agc_changed" for name, _ in events), (
            f"agc_changed not fired; got {[n for n, _ in events]}"
        )


class TestWrappedAgcFixture:
    """Command29 AGC fixture rejects malformed SET payloads."""

    @pytest.mark.parametrize(
        "inner",
        (
            b"",
            bytes([_SUB_AGC, _bcd(int(AgcMode.FAST)), 0x00]),
            bytes([_SUB_AGC, 0x1A]),
            bytes([_SUB_AGC, _bcd(4)]),
        ),
    )
    def test_agc_rejects_malformed_wrapped_payload(self, inner: bytes) -> None:
        mock = ToggleMockRadio()
        expected_agc = int(AgcMode.FAST)

        response = mock._dispatch_cmd29(
            _CMD_PREAMP, inner, _CONTROLLER_ADDR, RECEIVER_MAIN
        )

        assert response == mock._civ_nak(_CONTROLLER_ADDR, _RADIO_ADDR)
        assert mock._agc == expected_agc
        assert mock._dispatch_cmd29(
            _CMD_PREAMP, bytes([_SUB_AGC]), _CONTROLLER_ADDR, RECEIVER_MAIN
        ) == _build_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            _CMD_CMD29,
            data=bytes([RECEIVER_MAIN, _CMD_PREAMP, _SUB_AGC, _bcd(expected_agc)]),
        )


# ---------------------------------------------------------------------------
# 2. Audio Peak Filter
# ---------------------------------------------------------------------------


class TestAudioPeakFilterToggle:
    """APF mode cycle: OFF → WIDE → NAR (Command29, per-receiver)."""

    async def test_apf_default_off(self, toggle_radio: IcomRadio) -> None:
        mode = await toggle_radio.get_audio_peak_filter(receiver=RECEIVER_MAIN)
        assert mode == AudioPeakFilter.OFF

    async def test_apf_cycle_off_wide_nar(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """APF: OFF → WIDE → NAR → OFF on MAIN."""
        for target in (AudioPeakFilter.WIDE, AudioPeakFilter.NAR, AudioPeakFilter.OFF):
            await toggle_radio.set_audio_peak_filter(target, receiver=RECEIVER_MAIN)
            await asyncio.sleep(_SETTLE)
            got = await toggle_radio.get_audio_peak_filter(receiver=RECEIVER_MAIN)
            assert got == target, f"APF MAIN: expected {target}, got {got}"
            assert toggle_mock._apf[RECEIVER_MAIN] == int(target)

    async def test_apf_main_sub_independent(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """APF MAIN and SUB states are independent."""
        await toggle_radio.set_audio_peak_filter(
            AudioPeakFilter.WIDE, receiver=RECEIVER_MAIN
        )
        await toggle_radio.set_audio_peak_filter(
            AudioPeakFilter.NAR, receiver=RECEIVER_SUB
        )
        await asyncio.sleep(_SETTLE)

        main = await toggle_radio.get_audio_peak_filter(receiver=RECEIVER_MAIN)
        sub = await toggle_radio.get_audio_peak_filter(receiver=RECEIVER_SUB)
        assert main == AudioPeakFilter.WIDE
        assert sub == AudioPeakFilter.NAR

    async def test_apf_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited APF change is dispatched as audio_peak_filter_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_PREAMP,
            _SUB_AUDIO_PEAK_FILTER,
            data=bytes([int(AudioPeakFilter.MID)]),
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "audio_peak_filter_changed" for name, _ in events), (
            f"audio_peak_filter_changed not fired; got {[n for n, _ in events]}"
        )


# ---------------------------------------------------------------------------
# 3. Auto Notch
# ---------------------------------------------------------------------------


class TestAutoNotchToggle:
    """Auto notch off → on → off (Command29, per-receiver)."""

    async def test_auto_notch_default_off(self, toggle_radio: IcomRadio) -> None:
        state = await toggle_radio.get_auto_notch(receiver=RECEIVER_MAIN)
        assert state is False

    async def test_auto_notch_on_off_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Auto notch: off → on → off on MAIN."""
        await toggle_radio.set_auto_notch(True, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_auto_notch(receiver=RECEIVER_MAIN) is True
        assert toggle_mock._auto_notch[RECEIVER_MAIN] == 1

        await toggle_radio.set_auto_notch(False, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_auto_notch(receiver=RECEIVER_MAIN) is False
        assert toggle_mock._auto_notch[RECEIVER_MAIN] == 0

    async def test_auto_notch_main_sub_independent(
        self, toggle_radio: IcomRadio
    ) -> None:
        """Auto notch MAIN and SUB are toggled independently."""
        await toggle_radio.set_auto_notch(True, receiver=RECEIVER_MAIN)
        await toggle_radio.set_auto_notch(False, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await toggle_radio.get_auto_notch(receiver=RECEIVER_MAIN) is True
        assert await toggle_radio.get_auto_notch(receiver=RECEIVER_SUB) is False

    async def test_auto_notch_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited auto-notch frame fires auto_notch_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_PREAMP,
            _SUB_AUTO_NOTCH,
            data=b"\x01",
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "auto_notch_changed" for name, _ in events), (
            f"auto_notch_changed not fired; got {[n for n, _ in events]}"
        )
        matching = [d for n, d in events if n == "auto_notch_changed"]
        assert matching[0]["on"] is True


# ---------------------------------------------------------------------------
# 4. Compressor Status
# ---------------------------------------------------------------------------


class TestCompressorToggle:
    """Speech compressor off → on → off."""

    async def test_compressor_default_off(self, toggle_radio: IcomRadio) -> None:
        assert await toggle_radio.get_compressor() is False

    async def test_compressor_on_off_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        await toggle_radio.set_compressor(True)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_compressor() is True
        assert toggle_mock._compressor == 1

        await toggle_radio.set_compressor(False)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_compressor() is False
        assert toggle_mock._compressor == 0

    async def test_compressor_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited compressor frame fires compressor_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            _CMD_PREAMP,
            sub=_SUB_COMPRESSOR,
            data=b"\x01",
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "compressor_changed" for name, _ in events), (
            f"compressor_changed not fired; got {[n for n, _ in events]}"
        )
        matching = [d for n, d in events if n == "compressor_changed"]
        assert matching[0]["on"] is True


# ---------------------------------------------------------------------------
# 5. Monitor Status
# ---------------------------------------------------------------------------


class TestMonitorToggle:
    """Monitor off → on → off."""

    async def test_monitor_default_off(self, toggle_radio: IcomRadio) -> None:
        assert await toggle_radio.get_monitor() is False

    async def test_monitor_on_off_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        await toggle_radio.set_monitor(True)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_monitor() is True
        assert toggle_mock._monitor == 1

        await toggle_radio.set_monitor(False)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_monitor() is False
        assert toggle_mock._monitor == 0

    async def test_monitor_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited monitor frame fires monitor_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_civ(
            _CONTROLLER_ADDR, _RADIO_ADDR, _CMD_PREAMP, sub=_SUB_MONITOR, data=b"\x01"
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "monitor_changed" for name, _ in events), (
            f"monitor_changed not fired; got {[n for n, _ in events]}"
        )


# ---------------------------------------------------------------------------
# 6. VOX Status
# ---------------------------------------------------------------------------


class TestVoxToggle:
    """VOX off → on → off."""

    async def test_vox_default_off(self, toggle_radio: IcomRadio) -> None:
        assert await toggle_radio.get_vox() is False

    async def test_vox_on_off_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        await toggle_radio.set_vox(True)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_vox() is True
        assert toggle_mock._vox == 1

        await toggle_radio.set_vox(False)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_vox() is False
        assert toggle_mock._vox == 0

    async def test_vox_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited VOX frame fires vox_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_civ(
            _CONTROLLER_ADDR, _RADIO_ADDR, _CMD_PREAMP, sub=_SUB_VOX, data=b"\x01"
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "vox_changed" for name, _ in events), (
            f"vox_changed not fired; got {[n for n, _ in events]}"
        )


# ---------------------------------------------------------------------------
# 7. Break-In Status
# ---------------------------------------------------------------------------


class TestBreakInToggle:
    """Break-in mode cycle: OFF → SEMI → FULL → OFF."""

    async def test_break_in_default_off(self, toggle_radio: IcomRadio) -> None:
        mode = await toggle_radio.get_break_in()
        assert mode == BreakInMode.OFF

    async def test_break_in_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Break-in: OFF → SEMI → FULL → OFF."""
        for target in (BreakInMode.SEMI, BreakInMode.FULL, BreakInMode.OFF):
            await toggle_radio.set_break_in(target)
            await asyncio.sleep(_SETTLE)
            got = await toggle_radio.get_break_in()
            assert got == target, f"Break-in: expected {target}, got {got}"
            assert toggle_mock._break_in == int(target)

    async def test_break_in_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited break-in frame fires break_in_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            _CMD_PREAMP,
            sub=_SUB_BREAK_IN,
            data=bytes([_bcd(int(BreakInMode.SEMI))]),
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "break_in_changed" for name, _ in events), (
            f"break_in_changed not fired; got {[n for n, _ in events]}"
        )


# ---------------------------------------------------------------------------
# 8. Manual Notch
# ---------------------------------------------------------------------------


class TestManualNotchToggle:
    """Manual notch off → on → off (Command29, per-receiver)."""

    async def test_manual_notch_default_off(self, toggle_radio: IcomRadio) -> None:
        assert await toggle_radio.get_manual_notch(receiver=RECEIVER_MAIN) is False

    async def test_manual_notch_on_off_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Manual notch: off → on → off on MAIN."""
        await toggle_radio.set_manual_notch(True, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_manual_notch(receiver=RECEIVER_MAIN) is True
        assert toggle_mock._manual_notch[RECEIVER_MAIN] == 1

        await toggle_radio.set_manual_notch(False, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_manual_notch(receiver=RECEIVER_MAIN) is False
        assert toggle_mock._manual_notch[RECEIVER_MAIN] == 0

    async def test_manual_notch_main_sub_independent(
        self, toggle_radio: IcomRadio
    ) -> None:
        """Manual notch MAIN and SUB states are independent."""
        await toggle_radio.set_manual_notch(True, receiver=RECEIVER_MAIN)
        await toggle_radio.set_manual_notch(False, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await toggle_radio.get_manual_notch(receiver=RECEIVER_MAIN) is True
        assert await toggle_radio.get_manual_notch(receiver=RECEIVER_SUB) is False

    async def test_manual_notch_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited manual-notch frame fires manual_notch_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_PREAMP,
            _SUB_MANUAL_NOTCH,
            data=b"\x01",
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "manual_notch_changed" for name, _ in events), (
            f"manual_notch_changed not fired; got {[n for n, _ in events]}"
        )


# ---------------------------------------------------------------------------
# 9. Twin Peak Filter
# ---------------------------------------------------------------------------


class TestTwinPeakFilterToggle:
    """Twin peak filter off → on → off (Command29, per-receiver)."""

    async def test_twin_peak_default_off(self, toggle_radio: IcomRadio) -> None:
        assert await toggle_radio.get_twin_peak_filter(receiver=RECEIVER_MAIN) is False

    async def test_twin_peak_on_off_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Twin peak: off → on → off on MAIN."""
        await toggle_radio.set_twin_peak_filter(True, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_twin_peak_filter(receiver=RECEIVER_MAIN) is True
        assert toggle_mock._twin_peak[RECEIVER_MAIN] == 1

        await toggle_radio.set_twin_peak_filter(False, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_twin_peak_filter(receiver=RECEIVER_MAIN) is False
        assert toggle_mock._twin_peak[RECEIVER_MAIN] == 0

    async def test_twin_peak_main_sub_independent(
        self, toggle_radio: IcomRadio
    ) -> None:
        """Twin peak MAIN and SUB states are independent."""
        await toggle_radio.set_twin_peak_filter(True, receiver=RECEIVER_MAIN)
        await toggle_radio.set_twin_peak_filter(False, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await toggle_radio.get_twin_peak_filter(receiver=RECEIVER_MAIN) is True
        assert await toggle_radio.get_twin_peak_filter(receiver=RECEIVER_SUB) is False

    async def test_twin_peak_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited twin-peak frame fires twin_peak_filter_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_PREAMP,
            _SUB_TWIN_PEAK_FILTER,
            data=b"\x01",
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "twin_peak_filter_changed" for name, _ in events), (
            f"twin_peak_filter_changed not fired; got {[n for n, _ in events]}"
        )


# ---------------------------------------------------------------------------
# 10. Dial Lock Status
# ---------------------------------------------------------------------------


class TestDialLockToggle:
    """Dial lock off → on → off."""

    async def test_dial_lock_default_off(self, toggle_radio: IcomRadio) -> None:
        assert await toggle_radio.get_dial_lock() is False

    async def test_dial_lock_on_off_cycle(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        await toggle_radio.set_dial_lock(True)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_dial_lock() is True
        assert toggle_mock._dial_lock == 1

        await toggle_radio.set_dial_lock(False)
        await asyncio.sleep(_SETTLE)
        assert await toggle_radio.get_dial_lock() is False
        assert toggle_mock._dial_lock == 0

    async def test_dial_lock_unsolicited_update(
        self, toggle_radio: IcomRadio, toggle_mock: ToggleMockRadio
    ) -> None:
        """Unsolicited dial-lock frame fires dial_lock_changed."""
        events: list[tuple[str, dict]] = []
        toggle_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_civ(
            _CONTROLLER_ADDR, _RADIO_ADDR, _CMD_PREAMP, sub=_SUB_DIAL_LOCK, data=b"\x01"
        )
        toggle_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "dial_lock_changed" for name, _ in events), (
            f"dial_lock_changed not fired; got {[n for n, _ in events]}"
        )
        matching = [d for n, d in events if n == "dial_lock_changed"]
        assert matching[0]["on"] is True


# ---------------------------------------------------------------------------
# NAK / error handling
# ---------------------------------------------------------------------------


class TestNakHandling:
    """Verify radio raises on NAK responses from mock (unknown sub → NAK)."""

    async def test_unknown_sub_returns_nak(self, toggle_radio: IcomRadio) -> None:
        """Sending an unknown sub-command to ToggleMockRadio returns NAK.

        ToggleMockRadio.get_agc works, but a command aimed at an unhandled
        sub (0xFF) on a plain MockIcomRadio triggers NAK → radio raises.
        """
        from rigplane.exceptions import TimeoutError as IcomTimeoutError

        # Use a plain MockIcomRadio (no toggle support) to get NAK for 0x16 cmds.
        nak_mock = MockIcomRadio()
        await nak_mock.start()
        try:
            nak_radio = IcomRadio(
                host="127.0.0.1",
                port=nak_mock.control_port,
                username="testuser",
                password="testpass",
                timeout=1.0,
            )
            with fast_connect():
                await nak_radio.connect()
            try:
                with pytest.raises((ValueError, IcomTimeoutError, Exception)):
                    await nak_radio.get_agc()
            finally:
                await nak_radio.disconnect()
        finally:
            await nak_mock.stop()
