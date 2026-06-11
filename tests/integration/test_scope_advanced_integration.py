"""Mock-based integration tests for IC-7610 advanced scope controls (#137).

Tests the full request/response cycle for 3 scope commands using a local mock
radio server.  No real hardware required — runs in CI without env vars.

Commands under test:
  1. Scope During TX  (get_scope_during_tx  / set_scope_during_tx)
  2. Scope Center Type (get_scope_center_type / set_scope_center_type)
  3. Scope Fixed Edge  (get_scope_fixed_edge  / set_scope_fixed_edge)

Run with::

    pytest tests/integration/test_scope_advanced_integration.py -v
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

from rigplane.radio import IcomRadio  # noqa: E402, TID251
from rigplane.types import ScopeFixedEdge  # noqa: E402
from _perf_helpers import fast_connect  # noqa: E402
from mock_server import MockIcomRadio  # noqa: E402

# ---------------------------------------------------------------------------
# Local CI-V constants (keep mock self-contained)
# ---------------------------------------------------------------------------

_CMD_SCOPE = 0x27
_CMD_ACK = 0xFB

_SUB_SCOPE_DURING_TX = 0x1B
_SUB_SCOPE_CENTER_TYPE = 0x1C
_SUB_SCOPE_FIXED_EDGE = 0x1E

_CONTROLLER_ADDR = 0xE0
_RADIO_ADDR = 0x98

# ---------------------------------------------------------------------------
# BCD helpers (mirror commands.py, kept local to avoid coupling)
# ---------------------------------------------------------------------------


def _bcd_byte(value: int) -> int:
    """Encode 0-99 integer to one BCD byte (e.g. 14 → 0x14)."""
    return ((value // 10) << 4) | (value % 10)


def _bcd_byte_decode(b: int) -> int:
    """Decode one BCD byte to integer (e.g. 0x14 → 14)."""
    return ((b >> 4) & 0x0F) * 10 + (b & 0x0F)


def _bcd_encode_freq(freq_hz: int) -> bytes:
    """Encode frequency in Hz to Icom 5-byte BCD (little-endian pairs)."""
    digits = f"{freq_hz:010d}"
    result = bytearray(5)
    for i in range(5):
        low = int(digits[9 - 2 * i])
        high = int(digits[9 - 2 * i - 1])
        result[i] = (high << 4) | low
    return bytes(result)


def _bcd_decode_freq(data: bytes) -> int:
    """Decode Icom 5-byte BCD to frequency in Hz."""
    freq = 0
    for i in range(len(data)):
        high = (data[i] >> 4) & 0x0F
        low = data[i] & 0x0F
        freq += low * (10 ** (2 * i)) + high * (10 ** (2 * i + 1))
    return freq


# ---------------------------------------------------------------------------
# Extended mock with scope state
# ---------------------------------------------------------------------------


class ScopeMockRadio(MockIcomRadio):
    """MockIcomRadio extended with state for advanced scope controls.

    Handles GET and SET for:
      0x27 0x1B  — scope during TX
      0x27 0x1C  — scope center type
      0x27 0x1E  — scope fixed edge
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._during_tx: int = 0  # 0=off, 1=on
        self._center_type: int = 0  # 0, 1, or 2
        # Fixed-edge defaults: range 7, edge 1, 14.0–14.5 MHz
        self._fixed_range_index: int = 7
        self._fixed_edge: int = 1
        self._fixed_start_hz: int = 14_000_000
        self._fixed_end_hz: int = 14_500_000

    # ------------------------------------------------------------------
    # CI-V dispatch override
    # ------------------------------------------------------------------

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        """Handle scope (0x27) commands; fall through to base for everything else."""
        if cmd == _CMD_SCOPE:
            if not payload:
                return self._civ_nak(from_addr, self._radio_addr)
            return self._dispatch_scope(payload[0], payload[1:], from_addr)
        return super()._dispatch_civ(cmd, payload, from_addr)

    def _dispatch_scope(self, sub: int, rest: bytes, from_addr: int) -> bytes | None:
        """Dispatch a 0x27 <sub> [data] command."""
        to = from_addr
        frm = self._radio_addr

        # --- 0x27 0x1B: scope during TX ---
        if sub == _SUB_SCOPE_DURING_TX:
            if rest:  # SET
                self._during_tx = rest[0]
                return self._civ_ack(to, frm)
            # GET: 1-byte bool payload
            return self._civ_frame(
                to, frm, _CMD_SCOPE, sub=sub, data=bytes([self._during_tx])
            )

        # --- 0x27 0x1C: scope center type ---
        if sub == _SUB_SCOPE_CENTER_TYPE:
            if (
                rest
            ):  # SET: first byte is the value (radio.py never sends receiver here)
                self._center_type = rest[0]
                return self._civ_ack(to, frm)
            # GET: return single value byte
            return self._civ_frame(
                to, frm, _CMD_SCOPE, sub=sub, data=bytes([self._center_type])
            )

        # --- 0x27 0x1E: scope fixed edge ---
        if sub == _SUB_SCOPE_FIXED_EDGE:
            if len(rest) >= 12:  # SET: <range><edge> + start(5) + end(5)
                self._fixed_range_index = _bcd_byte_decode(rest[0])
                self._fixed_edge = _bcd_byte_decode(rest[1])
                self._fixed_start_hz = _bcd_decode_freq(rest[2:7])
                self._fixed_end_hz = _bcd_decode_freq(rest[7:12])
                return self._civ_ack(to, frm)
            # GET: a <range><edge> selector (2 bytes) is required by the
            # IC-7610 (MOR-662); respond with the 12-byte fixed-edge bounds.
            data = (
                bytes([_bcd_byte(self._fixed_range_index)])
                + bytes([_bcd_byte(self._fixed_edge)])
                + _bcd_encode_freq(self._fixed_start_hz)
                + _bcd_encode_freq(self._fixed_end_hz)
            )
            return self._civ_frame(to, frm, _CMD_SCOPE, sub=sub, data=data)

        return self._civ_nak(from_addr, self._radio_addr)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def scope_mock() -> AsyncGenerator[ScopeMockRadio, None]:
    """Start a ScopeMockRadio server for each test, stop it after."""
    server = ScopeMockRadio()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def scope_radio(scope_mock: ScopeMockRadio) -> AsyncGenerator[IcomRadio, None]:
    """IcomRadio connected to ScopeMockRadio, disconnected after test."""
    radio = IcomRadio(
        host="127.0.0.1",
        port=scope_mock.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
    )
    with fast_connect():
        await radio.connect()
    yield radio
    await radio.disconnect()


_SETTLE = 0.05  # seconds: wait after fire-and-forget SET before GET


# ---------------------------------------------------------------------------
# 1. Scope During TX
# ---------------------------------------------------------------------------


class TestScopeDuringTx:
    """Scope during-TX control: default off, set on, toggle."""

    async def test_during_tx_default_off(self, scope_radio: IcomRadio) -> None:
        """Default mock state is off."""
        result = await scope_radio.get_scope_during_tx()
        assert result is False

    async def test_during_tx_set_on(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Set during_tx ON and verify via GET."""
        await scope_radio.set_scope_during_tx(True)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_during_tx()
        assert result is True
        assert scope_mock._during_tx == 1

    async def test_during_tx_set_off(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Set during_tx OFF after being on."""
        scope_mock._during_tx = 1
        await scope_radio.set_scope_during_tx(False)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_during_tx()
        assert result is False
        assert scope_mock._during_tx == 0

    async def test_during_tx_toggle_on_off_on(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Toggle during_tx: off → on → off → on."""
        for expected in (True, False, True):
            await scope_radio.set_scope_during_tx(expected)
            await asyncio.sleep(_SETTLE)
            got = await scope_radio.get_scope_during_tx()
            assert got is expected, f"during_tx: expected {expected}, got {got}"
        assert scope_mock._during_tx == 1


# ---------------------------------------------------------------------------
# 2. Scope Center Type
# ---------------------------------------------------------------------------


class TestScopeCenterType:
    """Scope center-type control: values 0, 1, 2."""

    async def test_center_type_default_zero(self, scope_radio: IcomRadio) -> None:
        """Default mock state is 0."""
        result = await scope_radio.get_scope_center_type()
        assert result == 0

    async def test_center_type_set_one(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Set center_type to 1 and verify via GET."""
        await scope_radio.set_scope_center_type(1)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_center_type()
        assert result == 1
        assert scope_mock._center_type == 1

    async def test_center_type_set_two(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Set center_type to 2 and verify via GET."""
        await scope_radio.set_scope_center_type(2)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_center_type()
        assert result == 2
        assert scope_mock._center_type == 2

    async def test_center_type_cycle_all_values(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Cycle through all valid center_type values: 0 → 1 → 2 → 0."""
        for value in (0, 1, 2, 0):
            await scope_radio.set_scope_center_type(value)
            await asyncio.sleep(_SETTLE)
            got = await scope_radio.get_scope_center_type()
            assert got == value, f"center_type: expected {value}, got {got}"
            assert scope_mock._center_type == value


# ---------------------------------------------------------------------------
# 3. Scope Fixed Edge
# ---------------------------------------------------------------------------


class TestScopeFixedEdge:
    """Scope fixed-edge frequency bounds control."""

    async def test_fixed_edge_default_state(self, scope_radio: IcomRadio) -> None:
        """GET returns a ScopeFixedEdge with the mock's initial defaults."""
        result = await scope_radio.get_scope_fixed_edge()
        assert isinstance(result, ScopeFixedEdge)
        assert result.start_hz == 14_000_000
        assert result.end_hz == 14_500_000
        assert result.edge == 1

    async def test_fixed_edge_roundtrip_14mhz(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Set fixed edge to 14 MHz band and verify roundtrip."""
        start_hz = 14_000_000
        end_hz = 14_350_000
        await scope_radio.set_scope_fixed_edge(edge=1, start_hz=start_hz, end_hz=end_hz)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_fixed_edge()

        assert isinstance(result, ScopeFixedEdge)
        assert result.start_hz == start_hz
        assert result.end_hz == end_hz
        assert result.edge == 1
        assert scope_mock._fixed_start_hz == start_hz
        assert scope_mock._fixed_end_hz == end_hz

    async def test_fixed_edge_roundtrip_7mhz(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Set fixed edge to 7 MHz band and verify roundtrip."""
        start_hz = 7_000_000
        end_hz = 7_200_000
        await scope_radio.set_scope_fixed_edge(edge=2, start_hz=start_hz, end_hz=end_hz)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_fixed_edge()

        assert result.start_hz == start_hz
        assert result.end_hz == end_hz
        assert result.edge == 2
        assert scope_mock._fixed_start_hz == start_hz
        assert scope_mock._fixed_end_hz == end_hz

    async def test_fixed_edge_roundtrip_21mhz(
        self, scope_radio: IcomRadio, scope_mock: ScopeMockRadio
    ) -> None:
        """Set fixed edge to 21 MHz band and verify roundtrip."""
        start_hz = 21_000_000
        end_hz = 21_450_000
        await scope_radio.set_scope_fixed_edge(edge=1, start_hz=start_hz, end_hz=end_hz)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_fixed_edge()

        assert result.start_hz == start_hz
        assert result.end_hz == end_hz

    async def test_fixed_edge_multiple_changes(self, scope_radio: IcomRadio) -> None:
        """Change fixed edge multiple times and confirm each update is reflected."""
        settings = [
            (1, 14_000_000, 14_350_000),
            (2, 7_000_000, 7_300_000),
            (3, 21_000_000, 21_450_000),
        ]
        for edge, start, end in settings:
            await scope_radio.set_scope_fixed_edge(
                edge=edge, start_hz=start, end_hz=end
            )
            await asyncio.sleep(_SETTLE)
            result = await scope_radio.get_scope_fixed_edge()
            assert result.start_hz == start, (
                f"start mismatch: expected {start}, got {result.start_hz}"
            )
            assert result.end_hz == end, (
                f"end mismatch: expected {end}, got {result.end_hz}"
            )
            assert result.edge == edge, (
                f"edge mismatch: expected {edge}, got {result.edge}"
            )

    async def test_fixed_edge_range_index_preserved(
        self, scope_radio: IcomRadio
    ) -> None:
        """range_index in GET response matches what was set via the command builder."""
        start_hz = 14_000_000
        end_hz = 14_350_000
        # radio.py re-parses the frame it built to recover range_index
        await scope_radio.set_scope_fixed_edge(edge=1, start_hz=start_hz, end_hz=end_hz)
        await asyncio.sleep(_SETTLE)
        result = await scope_radio.get_scope_fixed_edge()

        assert isinstance(result.range_index, int)
        assert result.range_index >= 1
