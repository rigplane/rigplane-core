"""Mock-based integration tests for IC-7610 RIT/tuner commands (issue #136).

Tests the full request/response cycle for all 5 command families using a
local mock radio server.  No real hardware required — runs in CI without env vars.

Commands under test:
  1. RIT Frequency (0x21 sub 0x00) — get/set, ±9999 Hz
  2. RIT Status (0x21 sub 0x01) — get/set on/off
  3. RIT TX Status (0x21 sub 0x02) — get/set on/off
  4. Tuner Status (0x1C sub 0x01) — get/set 0/1/2
  5. TX Freq Monitor (0x1C sub 0x05) — get/set on/off

Run with::

    pytest tests/integration/test_rit_tuner_integration.py -v
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

# Make tests/ importable from tests/integration/
sys.path.insert(0, str(Path(__file__).parent.parent))

# All tests use MockIcomRadio — no real hardware required.
pytestmark = pytest.mark.mock_integration

from rigplane.radio import IcomRadio  # noqa: E402, TID251
from _perf_helpers import fast_connect  # noqa: E402
from mock_server import MockIcomRadio  # noqa: E402

# ---------------------------------------------------------------------------
# Local CI-V constants (keep mock self-contained)
# ---------------------------------------------------------------------------

_CMD_RIT = 0x21
_SUB_RIT_FREQ = 0x00
_SUB_RIT_STATUS = 0x01
_SUB_RIT_TX = 0x02

_CMD_TUNER = 0x1C
_SUB_TUNER_STATUS = 0x01
_SUB_TX_FREQ_MON = 0x03

_SETTLE = 0.05  # seconds: wait after fire-and-forget SET before GET


# ---------------------------------------------------------------------------
# RIT signed BCD helpers
# ---------------------------------------------------------------------------


def _encode_rit_offset(hz: int) -> bytes:
    """Encode RIT offset to 3-byte signed BCD (range ±9999 Hz).

    Wire format (matches commands.py set_rit_frequency):
      byte[0] = d0: (tens << 4) | units   (LSB first)
      byte[1] = d1: (thousands << 4) | hundreds
      byte[2] = sign: 0x00 positive, 0x01 negative
    """
    if not -9999 <= hz <= 9999:
        raise ValueError(f"RIT offset must be -9999 to +9999 Hz, got {hz}")

    abs_hz = abs(hz)
    d0 = ((abs_hz % 100 // 10) << 4) | (abs_hz % 10)
    d1 = ((abs_hz % 10000 // 1000) << 4) | (abs_hz % 1000 // 100)
    sign = 0x01 if hz < 0 else 0x00

    return bytes([d0, d1, sign])


def _decode_rit_offset(data: bytes) -> int:
    """Decode 3-byte signed BCD to RIT offset (Hz).

    Mirrors parse_rit_frequency_response in commands.py.
    """
    if len(data) < 3:
        return 0

    d0, d1, sign = data[0], data[1], data[2]
    hz = (d1 >> 4) * 1000 + (d1 & 0x0F) * 100 + (d0 >> 4) * 10 + (d0 & 0x0F)
    return -hz if sign else hz


# ---------------------------------------------------------------------------
# Extended mock with RIT/tuner state
# ---------------------------------------------------------------------------


class RitTunerMockRadio(MockIcomRadio):
    """MockIcomRadio extended with RIT and tuner state.

    Handles:
      - 0x21 / sub 0x00: RIT frequency offset get/set (3-byte signed BCD)
      - 0x21 / sub 0x01: RIT on/off
      - 0x21 / sub 0x02: RIT TX on/off
      - 0x1C / sub 0x01: tuner status get/set (0=off, 1=on, 2=tuning)
      - 0x1C / sub 0x05: TX freq monitor on/off

    All other commands are forwarded to the parent MockIcomRadio.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._rit_offset_hz: int = 0
        self._rit_on: int = 0  # 0=off, 1=on
        self._rit_tx_on: int = 0  # 0=off, 1=on
        self._tuner_status: int = 0  # 0=off, 1=on, 2=tuning
        self._tx_freq_mon: int = 0  # 0=off, 1=on

    # ------------------------------------------------------------------
    # CI-V dispatch override
    # ------------------------------------------------------------------

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        """Intercept RIT (0x21) and tuner/TX-mon (0x1C) commands."""
        if cmd == _CMD_RIT:
            return self._dispatch_rit(payload, from_addr)
        if cmd == _CMD_TUNER:
            return self._dispatch_tuner(payload, from_addr)
        return super()._dispatch_civ(cmd, payload, from_addr)

    # ------------------------------------------------------------------
    # RIT dispatch (0x21)
    # ------------------------------------------------------------------

    def _dispatch_rit(self, payload: bytes, from_addr: int) -> bytes:
        """Dispatch RIT commands (cmd 0x21)."""
        if not payload:
            return self._civ_nak(from_addr, self._radio_addr)

        sub = payload[0]
        rest = payload[1:]

        if sub == _SUB_RIT_FREQ:
            return self._handle_rit_freq(rest, from_addr)
        if sub == _SUB_RIT_STATUS:
            return self._handle_rit_status(rest, from_addr)
        if sub == _SUB_RIT_TX:
            return self._handle_rit_tx(rest, from_addr)
        return self._civ_nak(from_addr, self._radio_addr)

    def _handle_rit_freq(self, rest: bytes, from_addr: int) -> bytes:
        """Handle RIT frequency offset get/set (0x21 sub 0x00)."""
        to = from_addr
        frm = self._radio_addr

        if len(rest) >= 3:  # SET
            self._rit_offset_hz = _decode_rit_offset(rest[:3])
            return self._civ_ack(to, frm)

        # GET
        return self._civ_frame(
            to,
            frm,
            _CMD_RIT,
            sub=_SUB_RIT_FREQ,
            data=_encode_rit_offset(self._rit_offset_hz),
        )

    def _handle_rit_status(self, rest: bytes, from_addr: int) -> bytes:
        """Handle RIT on/off (0x21 sub 0x01)."""
        to = from_addr
        frm = self._radio_addr

        if rest:  # SET
            self._rit_on = rest[0]
            return self._civ_ack(to, frm)

        # GET
        return self._civ_frame(
            to,
            frm,
            _CMD_RIT,
            sub=_SUB_RIT_STATUS,
            data=bytes([self._rit_on]),
        )

    def _handle_rit_tx(self, rest: bytes, from_addr: int) -> bytes:
        """Handle RIT TX on/off (0x21 sub 0x02)."""
        to = from_addr
        frm = self._radio_addr

        if rest:  # SET
            self._rit_tx_on = rest[0]
            return self._civ_ack(to, frm)

        # GET
        return self._civ_frame(
            to,
            frm,
            _CMD_RIT,
            sub=_SUB_RIT_TX,
            data=bytes([self._rit_tx_on]),
        )

    # ------------------------------------------------------------------
    # Tuner / TX freq monitor dispatch (0x1C)
    # ------------------------------------------------------------------

    def _dispatch_tuner(self, payload: bytes, from_addr: int) -> bytes:
        """Dispatch tuner/TX-freq-monitor commands (cmd 0x1C)."""
        if not payload:
            return self._civ_nak(from_addr, self._radio_addr)

        sub = payload[0]
        rest = payload[1:]

        if sub == _SUB_TUNER_STATUS:
            return self._handle_tuner_status(rest, from_addr)
        if sub == _SUB_TX_FREQ_MON:
            return self._handle_tx_freq_mon(rest, from_addr)
        return self._civ_nak(from_addr, self._radio_addr)

    def _handle_tuner_status(self, rest: bytes, from_addr: int) -> bytes:
        """Handle tuner status (0x1C sub 0x01): 0=off, 1=on, 2=tuning."""
        to = from_addr
        frm = self._radio_addr

        if rest:  # SET
            self._tuner_status = rest[0]
            return self._civ_ack(to, frm)

        # GET
        return self._civ_frame(
            to,
            frm,
            _CMD_TUNER,
            sub=_SUB_TUNER_STATUS,
            data=bytes([self._tuner_status]),
        )

    def _handle_tx_freq_mon(self, rest: bytes, from_addr: int) -> bytes:
        """Handle TX freq monitor on/off (0x1C sub 0x05)."""
        to = from_addr
        frm = self._radio_addr

        if rest:  # SET
            self._tx_freq_mon = rest[0]
            return self._civ_ack(to, frm)

        # GET
        return self._civ_frame(
            to,
            frm,
            _CMD_TUNER,
            sub=_SUB_TX_FREQ_MON,
            data=bytes([self._tx_freq_mon]),
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def rit_tuner_mock() -> AsyncGenerator[RitTunerMockRadio, None]:
    """Start a RitTunerMockRadio server for each test, stop it after."""
    server = RitTunerMockRadio()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def rit_tuner_radio(
    rit_tuner_mock: RitTunerMockRadio,
) -> AsyncGenerator[IcomRadio, None]:
    """IcomRadio connected to RitTunerMockRadio, disconnected after each test."""
    radio = IcomRadio(
        host="127.0.0.1",
        port=rit_tuner_mock.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
    )
    with fast_connect():
        await radio.connect()
    yield radio
    await radio.disconnect()


# ---------------------------------------------------------------------------
# 1. RIT Frequency
# ---------------------------------------------------------------------------


class TestRitFrequency:
    """RIT frequency offset (±9999 Hz) roundtrip tests."""

    async def test_default_offset_zero(self, rit_tuner_radio: IcomRadio) -> None:
        """Default RIT offset is 0 Hz."""
        result = await rit_tuner_radio.get_rit_frequency()
        assert result == 0

    async def test_set_positive_150hz(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT offset to +150 Hz and verify roundtrip."""
        await rit_tuner_radio.set_rit_frequency(150)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_frequency()
        assert result == 150
        assert rit_tuner_mock._rit_offset_hz == 150

    async def test_set_negative_200hz(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT offset to -200 Hz and verify roundtrip."""
        await rit_tuner_radio.set_rit_frequency(-200)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_frequency()
        assert result == -200
        assert rit_tuner_mock._rit_offset_hz == -200

    async def test_set_max_positive(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT offset to +9999 Hz (maximum)."""
        await rit_tuner_radio.set_rit_frequency(9999)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_frequency()
        assert result == 9999
        assert rit_tuner_mock._rit_offset_hz == 9999

    async def test_set_max_negative(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT offset to -9999 Hz (minimum)."""
        await rit_tuner_radio.set_rit_frequency(-9999)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_frequency()
        assert result == -9999
        assert rit_tuner_mock._rit_offset_hz == -9999

    async def test_multiple_changes(self, rit_tuner_radio: IcomRadio) -> None:
        """Change RIT offset through several values and verify each."""
        for offset in (150, -200, 0, 1000, -500, 9999, -9999):
            await rit_tuner_radio.set_rit_frequency(offset)
            await asyncio.sleep(_SETTLE)
            result = await rit_tuner_radio.get_rit_frequency()
            assert result == offset, f"Expected {offset} Hz, got {result} Hz"

    async def test_reset_to_zero(self, rit_tuner_radio: IcomRadio) -> None:
        """Set non-zero offset then reset to 0."""
        await rit_tuner_radio.set_rit_frequency(500)
        await asyncio.sleep(_SETTLE)

        await rit_tuner_radio.set_rit_frequency(0)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_frequency()
        assert result == 0


# ---------------------------------------------------------------------------
# 2. RIT Status
# ---------------------------------------------------------------------------


class TestRitStatus:
    """RIT on/off roundtrip tests."""

    async def test_default_off(self, rit_tuner_radio: IcomRadio) -> None:
        """Default RIT status is OFF."""
        result = await rit_tuner_radio.get_rit_status()
        assert result is False

    async def test_set_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT ON and verify via GET."""
        await rit_tuner_radio.set_rit_status(True)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_status()
        assert result is True
        assert rit_tuner_mock._rit_on == 1

    async def test_set_off_after_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT ON then OFF; GET returns False."""
        await rit_tuner_radio.set_rit_status(True)
        await asyncio.sleep(_SETTLE)

        await rit_tuner_radio.set_rit_status(False)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_status()
        assert result is False
        assert rit_tuner_mock._rit_on == 0

    async def test_toggle_on_off_on(self, rit_tuner_radio: IcomRadio) -> None:
        """Toggle RIT on → off → on."""
        await rit_tuner_radio.set_rit_status(True)
        await asyncio.sleep(_SETTLE)
        assert await rit_tuner_radio.get_rit_status() is True

        await rit_tuner_radio.set_rit_status(False)
        await asyncio.sleep(_SETTLE)
        assert await rit_tuner_radio.get_rit_status() is False

        await rit_tuner_radio.set_rit_status(True)
        await asyncio.sleep(_SETTLE)
        assert await rit_tuner_radio.get_rit_status() is True


# ---------------------------------------------------------------------------
# 3. RIT TX Status
# ---------------------------------------------------------------------------


class TestRitTxStatus:
    """RIT TX on/off roundtrip tests."""

    async def test_default_off(self, rit_tuner_radio: IcomRadio) -> None:
        """Default RIT TX status is OFF."""
        result = await rit_tuner_radio.get_rit_tx_status()
        assert result is False

    async def test_set_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT TX ON and verify via GET."""
        await rit_tuner_radio.set_rit_tx_status(True)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_tx_status()
        assert result is True
        assert rit_tuner_mock._rit_tx_on == 1

    async def test_set_off_after_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set RIT TX ON then OFF; GET returns False."""
        await rit_tuner_radio.set_rit_tx_status(True)
        await asyncio.sleep(_SETTLE)

        await rit_tuner_radio.set_rit_tx_status(False)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_rit_tx_status()
        assert result is False
        assert rit_tuner_mock._rit_tx_on == 0

    async def test_rit_and_rit_tx_independent(self, rit_tuner_radio: IcomRadio) -> None:
        """RIT status and RIT TX status are independent flags."""
        await rit_tuner_radio.set_rit_status(True)
        await rit_tuner_radio.set_rit_tx_status(False)
        await asyncio.sleep(_SETTLE)

        assert await rit_tuner_radio.get_rit_status() is True
        assert await rit_tuner_radio.get_rit_tx_status() is False

        await rit_tuner_radio.set_rit_status(False)
        await rit_tuner_radio.set_rit_tx_status(True)
        await asyncio.sleep(_SETTLE)

        assert await rit_tuner_radio.get_rit_status() is False
        assert await rit_tuner_radio.get_rit_tx_status() is True


# ---------------------------------------------------------------------------
# 4. Tuner Status
# ---------------------------------------------------------------------------


class TestTunerStatus:
    """Tuner status (0=off, 1=on, 2=tuning) roundtrip tests."""

    async def test_default_off(self, rit_tuner_radio: IcomRadio) -> None:
        """Default tuner status is OFF (0)."""
        result = await rit_tuner_radio.get_tuner_status()
        assert result == 0

    async def test_set_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set tuner ON (1) and verify via GET."""
        await rit_tuner_radio.set_tuner_status(1)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_tuner_status()
        assert result == 1
        assert rit_tuner_mock._tuner_status == 1

    async def test_set_tuning(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set tuner TUNING (2) and verify via GET."""
        await rit_tuner_radio.set_tuner_status(2)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_tuner_status()
        assert result == 2
        assert rit_tuner_mock._tuner_status == 2

    async def test_cycle_all_states(self, rit_tuner_radio: IcomRadio) -> None:
        """Cycle through all tuner states: 0 → 1 → 2 → 0."""
        for state in (0, 1, 2, 0):
            await rit_tuner_radio.set_tuner_status(state)
            await asyncio.sleep(_SETTLE)
            result = await rit_tuner_radio.get_tuner_status()
            assert result == state, f"Expected tuner state {state}, got {result}"

    async def test_set_off_from_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set tuner ON then OFF; GET returns 0."""
        await rit_tuner_radio.set_tuner_status(1)
        await asyncio.sleep(_SETTLE)

        await rit_tuner_radio.set_tuner_status(0)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_tuner_status()
        assert result == 0
        assert rit_tuner_mock._tuner_status == 0


# ---------------------------------------------------------------------------
# 5. TX Freq Monitor
# ---------------------------------------------------------------------------


class TestTxFreqMonitor:
    """TX frequency monitor on/off roundtrip tests."""

    async def test_default_off(self, rit_tuner_radio: IcomRadio) -> None:
        """Default TX freq monitor is OFF."""
        result = await rit_tuner_radio.get_tx_freq_monitor()
        assert result is False

    async def test_set_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set TX freq monitor ON and verify via GET."""
        await rit_tuner_radio.set_tx_freq_monitor(True)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_tx_freq_monitor()
        assert result is True
        assert rit_tuner_mock._tx_freq_mon == 1

    async def test_set_off_after_on(
        self, rit_tuner_radio: IcomRadio, rit_tuner_mock: RitTunerMockRadio
    ) -> None:
        """Set TX freq monitor ON then OFF; GET returns False."""
        await rit_tuner_radio.set_tx_freq_monitor(True)
        await asyncio.sleep(_SETTLE)

        await rit_tuner_radio.set_tx_freq_monitor(False)
        await asyncio.sleep(_SETTLE)

        result = await rit_tuner_radio.get_tx_freq_monitor()
        assert result is False
        assert rit_tuner_mock._tx_freq_mon == 0

    async def test_toggle_on_off_on(self, rit_tuner_radio: IcomRadio) -> None:
        """Toggle TX freq monitor on → off → on."""
        await rit_tuner_radio.set_tx_freq_monitor(True)
        await asyncio.sleep(_SETTLE)
        assert await rit_tuner_radio.get_tx_freq_monitor() is True

        await rit_tuner_radio.set_tx_freq_monitor(False)
        await asyncio.sleep(_SETTLE)
        assert await rit_tuner_radio.get_tx_freq_monitor() is False

        await rit_tuner_radio.set_tx_freq_monitor(True)
        await asyncio.sleep(_SETTLE)
        assert await rit_tuner_radio.get_tx_freq_monitor() is True


# ---------------------------------------------------------------------------
# RIT BCD codec unit checks (no radio needed)
# ---------------------------------------------------------------------------


class TestRitBcdCodec:
    """Verify the mock's RIT BCD encode/decode helpers are self-consistent."""

    @pytest.mark.parametrize(
        "hz",
        [
            0,
            1,
            9,
            10,
            99,
            100,
            999,
            1000,
            9999,
            -1,
            -9,
            -10,
            -99,
            -100,
            -999,
            -1000,
            -9999,
            150,
            -200,
            500,
            -500,
            1234,
            -5678,
        ],
    )
    def test_encode_decode_roundtrip(self, hz: int) -> None:
        """encode → decode must recover the original offset."""
        encoded = _encode_rit_offset(hz)
        assert len(encoded) == 3
        decoded = _decode_rit_offset(encoded)
        assert decoded == hz, f"Roundtrip failed for {hz} Hz: got {decoded}"
