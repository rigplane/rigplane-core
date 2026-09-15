"""Mock-based integration tests for DSP level commands (issue #130).

Tests the full request/response cycle for all 10 DSP level commands using a
local mock radio server.  No real hardware required — runs in CI without env vars.

Commands under test:
  Part 1 (level-style, int 0-255):
    1. APF Type Level
    2. NR Level
    3. PBT Inner
    4. PBT Outer
    5. NB Level
  Part 2 (special cases):
    6. DIGI-SEL Shift (int 0-255)
    7. AGC Time Constant (BCD 0-13)
    8. Filter Shape (enum)
    9. SSB TX Bandwidth (enum, global)
    10. AF Mute (bool)

Run with::

    pytest tests/integration/test_dsp_levels_integration.py -v
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
from rigplane.types import FilterShape, SsbTxBandwidth  # noqa: E402
from _perf_helpers import fast_connect  # noqa: E402
from mock_server import MockIcomRadio  # noqa: E402

# ---------------------------------------------------------------------------
# Local CI-V constants (keep mock self-contained)
# ---------------------------------------------------------------------------

_CMD_LEVEL = 0x14
_CMD_PREAMP = 0x16
_CMD_CTL_MEM = 0x1A
_CMD_CMD29 = 0x29

_SUB_APF_TYPE_LEVEL = 0x05
_SUB_NR_LEVEL = 0x06
_SUB_PBT_INNER = 0x07
_SUB_PBT_OUTER = 0x08
_SUB_NB_LEVEL = 0x12
_SUB_DIGISEL_SHIFT = 0x13
_SUB_FILTER_SHAPE = 0x56
_SUB_SSB_TX_BANDWIDTH = 0x58
_SUB_AGC_TIME_CONSTANT = 0x04
_SUB_AF_MUTE = 0x09

_CONTROLLER_ADDR = 0xE0
_RADIO_ADDR = 0x98


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------


def _bcd_byte(v: int) -> int:
    """Encode 0-99 to one BCD byte (e.g. 13 → 0x13)."""
    return ((v // 10) << 4) | (v % 10)


def _level_bcd_encode(value: int) -> bytes:
    """Encode 0-9999 level to 2-byte BCD (e.g. 128 → b'\\x01\\x28')."""
    d = f"{value:04d}"
    return bytes([(int(d[0]) << 4) | int(d[1]), (int(d[2]) << 4) | int(d[3])])


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
# Extended mock with DSP level state
# ---------------------------------------------------------------------------


class DspLevelsMockRadio(MockIcomRadio):
    """MockIcomRadio extended with DSP level state for all 10 DSP commands.

    Handles cmd29-wrapped 0x14 (level), 0x16 (filter shape), 0x1A (AGC time
    constant, AF mute) commands, plus direct 0x16/0x58 (SSB TX bandwidth).
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        # Per-receiver level state (receiver 0=MAIN, 1=SUB)
        self._apf_level: dict[int, int] = {0: 128, 1: 128}
        self._nr_level: dict[int, int] = {0: 0, 1: 0}
        self._pbt_inner: dict[int, int] = {0: 128, 1: 128}
        self._pbt_outer: dict[int, int] = {0: 128, 1: 128}
        self._nb_level: dict[int, int] = {0: 0, 1: 0}
        self._digisel_shift: dict[int, int] = {0: 128, 1: 128}
        self._filter_shape: dict[int, int] = {0: 0, 1: 0}  # 0=SHARP
        self._agc_time_constant: dict[int, int] = {0: 3, 1: 3}
        self._af_mute: dict[int, bool] = {0: False, 1: False}
        # Global state
        self._ssb_tx_bandwidth: int = 0  # WIDE
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
        """Handle direct 0x16/0x58 SSB TX bandwidth in addition to base commands."""
        to = from_addr
        frm = self._radio_addr

        if cmd == _CMD_PREAMP:
            if not payload:
                return self._civ_nak(to, frm)
            sub = payload[0]
            rest = payload[1:]
            if sub == _SUB_SSB_TX_BANDWIDTH:
                if rest:  # SET — single BCD byte
                    raw = rest[0]
                    self._ssb_tx_bandwidth = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                    return self._civ_ack(to, frm)
                # GET
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_PREAMP,
                    sub=_SUB_SSB_TX_BANDWIDTH,
                    data=bytes([_bcd_byte(self._ssb_tx_bandwidth)]),
                )
            return self._civ_nak(to, frm)

        return super()._dispatch_civ(cmd, payload, from_addr)

    def _dispatch_cmd29(
        self, real_cmd: int, inner: bytes, from_addr: int, receiver: int
    ) -> bytes | None:
        """Handle DSP level commands in Command29-wrapped format."""
        to = from_addr
        frm = self._radio_addr

        # Level commands (0x14): APF, NR, PBT inner/outer, NB, DIGI-SEL shift
        if real_cmd == _CMD_LEVEL:
            if not inner:
                return self._civ_nak(to, frm)
            sub = inner[0]
            rest = inner[1:]
            _level_stores: dict[int, dict[int, int]] = {
                _SUB_APF_TYPE_LEVEL: self._apf_level,
                _SUB_NR_LEVEL: self._nr_level,
                _SUB_PBT_INNER: self._pbt_inner,
                _SUB_PBT_OUTER: self._pbt_outer,
                _SUB_NB_LEVEL: self._nb_level,
                _SUB_DIGISEL_SHIFT: self._digisel_shift,
            }
            if sub in _level_stores:
                store = _level_stores[sub]
                if rest:  # SET — decode 2-byte BCD
                    store[receiver] = self._decode_level_bcd(rest)
                    return self._civ_ack(to, frm)
                # GET — respond wrapped in cmd29
                level = store.get(receiver, 0)
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes([receiver, _CMD_LEVEL, sub]) + _level_bcd_encode(level),
                )
            return self._civ_nak(to, frm)

        # Filter shape (0x16/0x56, cmd29, per-receiver)
        if real_cmd == _CMD_PREAMP and inner and inner[0] == _SUB_FILTER_SHAPE:
            rest = inner[1:]
            if rest:  # SET — single BCD byte
                raw = rest[0]
                self._filter_shape[receiver] = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                return self._civ_ack(to, frm)
            # GET
            shape = self._filter_shape.get(receiver, 0)
            return self._civ_frame(
                to,
                frm,
                _CMD_CMD29,
                data=bytes(
                    [receiver, _CMD_PREAMP, _SUB_FILTER_SHAPE, _bcd_byte(shape)]
                ),
            )

        # CTL_MEM commands (0x1A): AGC time constant, AF mute
        if real_cmd == _CMD_CTL_MEM:
            if not inner:
                return self._civ_nak(to, frm)
            sub = inner[0]
            rest = inner[1:]

            if sub == _SUB_AGC_TIME_CONSTANT:
                if rest:  # SET — single BCD byte
                    raw = rest[0]
                    self._agc_time_constant[receiver] = ((raw >> 4) & 0x0F) * 10 + (
                        raw & 0x0F
                    )
                    return self._civ_ack(to, frm)
                # GET
                val = self._agc_time_constant.get(receiver, 3)
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [receiver, _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, _bcd_byte(val)]
                    ),
                )

            if sub == _SUB_AF_MUTE:
                if rest:  # SET — bool byte
                    self._af_mute[receiver] = bool(rest[0])
                    return self._civ_ack(to, frm)
                # GET
                on = self._af_mute.get(receiver, False)
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [receiver, _CMD_CTL_MEM, _SUB_AF_MUTE, 0x01 if on else 0x00]
                    ),
                )

            return self._civ_nak(to, frm)

        # Fall through to parent for ATT (0x11), PREAMP (0x16/0x02), DIGI-SEL (0x16/0x4E)
        return super()._dispatch_cmd29(real_cmd, inner, from_addr, receiver)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def dsp_mock() -> AsyncGenerator[DspLevelsMockRadio, None]:
    """Start a DspLevelsMockRadio server for each test, stop it after."""
    server = DspLevelsMockRadio()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def dsp_radio(dsp_mock: DspLevelsMockRadio) -> AsyncGenerator[IcomRadio, None]:
    """IcomRadio connected to DspLevelsMockRadio, disconnected after test."""
    radio = IcomRadio(
        host="127.0.0.1",
        port=dsp_mock.control_port,
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
# Helper
# ---------------------------------------------------------------------------

_SETTLE = 0.05  # seconds: wait after fire-and-forget SET before GET


# ---------------------------------------------------------------------------
# 1. APF Type Level
# ---------------------------------------------------------------------------


class TestApfTypeLevel:
    """APF Type Level (0x14/0x05, cmd29, per-receiver)."""

    async def test_apf_level_default(self, dsp_radio: IcomRadio) -> None:
        """Default APF level is 128 (mid-point)."""
        level = await dsp_radio.get_apf_type_level(receiver=RECEIVER_MAIN)
        assert level == 128

    async def test_apf_level_roundtrip_main(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set APF level 150 on MAIN, verify GET returns 150."""
        await dsp_radio.set_apf_type_level(150, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_apf_type_level(receiver=RECEIVER_MAIN)
        assert got == 150
        assert dsp_mock._apf_level[RECEIVER_MAIN] == 150

    async def test_apf_level_main_sub_independent(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """MAIN and SUB APF levels are independent."""
        await dsp_radio.set_apf_type_level(100, receiver=RECEIVER_MAIN)
        await dsp_radio.set_apf_type_level(200, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        main = await dsp_radio.get_apf_type_level(receiver=RECEIVER_MAIN)
        sub = await dsp_radio.get_apf_type_level(receiver=RECEIVER_SUB)
        assert main == 100
        assert sub == 200

    async def test_apf_level_boundary_min(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """APF level 0 (minimum) round-trips correctly."""
        await dsp_radio.set_apf_type_level(0, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_apf_type_level(receiver=RECEIVER_MAIN) == 0

    async def test_apf_level_boundary_max(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """APF level 255 (maximum) round-trips correctly."""
        await dsp_radio.set_apf_type_level(255, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_apf_type_level(receiver=RECEIVER_MAIN) == 255

    async def test_apf_level_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited APF level update; GET reflects new value."""
        dsp_mock._apf_level[RECEIVER_MAIN] = 75
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_LEVEL,
            _SUB_APF_TYPE_LEVEL,
            data=_level_bcd_encode(75),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        got = await dsp_radio.get_apf_type_level(receiver=RECEIVER_MAIN)
        assert got == 75


# ---------------------------------------------------------------------------
# 2. NR Level
# ---------------------------------------------------------------------------


class TestNrLevel:
    """NR Level (0x14/0x06, cmd29, per-receiver)."""

    async def test_nr_level_default(self, dsp_radio: IcomRadio) -> None:
        """Default NR level is 0."""
        level = await dsp_radio.get_nr_level(receiver=RECEIVER_MAIN)
        assert level == 0

    async def test_nr_level_roundtrip_main(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set NR level 150 on MAIN, verify GET returns 150."""
        await dsp_radio.set_nr_level(150, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_nr_level(receiver=RECEIVER_MAIN)
        assert got == 150
        assert dsp_mock._nr_level[RECEIVER_MAIN] == 150

    async def test_nr_level_main_sub_independent(self, dsp_radio: IcomRadio) -> None:
        """MAIN and SUB NR levels are independent."""
        await dsp_radio.set_nr_level(100, receiver=RECEIVER_MAIN)
        await dsp_radio.set_nr_level(200, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await dsp_radio.get_nr_level(receiver=RECEIVER_MAIN) == 100
        assert await dsp_radio.get_nr_level(receiver=RECEIVER_SUB) == 200

    async def test_nr_level_boundary_min(self, dsp_radio: IcomRadio) -> None:
        """NR level 0 round-trips correctly."""
        await dsp_radio.set_nr_level(0, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_nr_level(receiver=RECEIVER_MAIN) == 0

    async def test_nr_level_boundary_max(self, dsp_radio: IcomRadio) -> None:
        """NR level 255 round-trips correctly."""
        await dsp_radio.set_nr_level(255, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_nr_level(receiver=RECEIVER_MAIN) == 255

    async def test_nr_level_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited NR level update; GET reflects new value."""
        dsp_mock._nr_level[RECEIVER_MAIN] = 180
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_LEVEL,
            _SUB_NR_LEVEL,
            data=_level_bcd_encode(180),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        assert await dsp_radio.get_nr_level(receiver=RECEIVER_MAIN) == 180


# ---------------------------------------------------------------------------
# 3. PBT Inner
# ---------------------------------------------------------------------------


class TestPbtInner:
    """PBT Inner (0x14/0x07, cmd29, per-receiver)."""

    async def test_pbt_inner_default(self, dsp_radio: IcomRadio) -> None:
        """Default PBT Inner is 128 (center)."""
        assert await dsp_radio.get_pbt_inner(receiver=RECEIVER_MAIN) == 128

    async def test_pbt_inner_roundtrip_main(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set PBT Inner 50 on MAIN, verify GET returns 50."""
        await dsp_radio.set_pbt_inner(50, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_pbt_inner(receiver=RECEIVER_MAIN)
        assert got == 50
        assert dsp_mock._pbt_inner[RECEIVER_MAIN] == 50

    async def test_pbt_inner_main_sub_independent(self, dsp_radio: IcomRadio) -> None:
        """MAIN and SUB PBT Inner are independent."""
        await dsp_radio.set_pbt_inner(100, receiver=RECEIVER_MAIN)
        await dsp_radio.set_pbt_inner(200, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await dsp_radio.get_pbt_inner(receiver=RECEIVER_MAIN) == 100
        assert await dsp_radio.get_pbt_inner(receiver=RECEIVER_SUB) == 200

    async def test_pbt_inner_boundary_min(self, dsp_radio: IcomRadio) -> None:
        """PBT Inner 0 round-trips correctly."""
        await dsp_radio.set_pbt_inner(0, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_pbt_inner(receiver=RECEIVER_MAIN) == 0

    async def test_pbt_inner_boundary_max(self, dsp_radio: IcomRadio) -> None:
        """PBT Inner 255 round-trips correctly."""
        await dsp_radio.set_pbt_inner(255, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_pbt_inner(receiver=RECEIVER_MAIN) == 255

    async def test_pbt_inner_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited PBT Inner update; GET reflects new value."""
        dsp_mock._pbt_inner[RECEIVER_MAIN] = 60
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_LEVEL,
            _SUB_PBT_INNER,
            data=_level_bcd_encode(60),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        assert await dsp_radio.get_pbt_inner(receiver=RECEIVER_MAIN) == 60


# ---------------------------------------------------------------------------
# 4. PBT Outer
# ---------------------------------------------------------------------------


class TestPbtOuter:
    """PBT Outer (0x14/0x08, cmd29, per-receiver)."""

    async def test_pbt_outer_default(self, dsp_radio: IcomRadio) -> None:
        """Default PBT Outer is 128 (center)."""
        assert await dsp_radio.get_pbt_outer(receiver=RECEIVER_MAIN) == 128

    async def test_pbt_outer_roundtrip_main(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set PBT Outer 75 on MAIN, verify GET returns 75."""
        await dsp_radio.set_pbt_outer(75, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_pbt_outer(receiver=RECEIVER_MAIN)
        assert got == 75
        assert dsp_mock._pbt_outer[RECEIVER_MAIN] == 75

    async def test_pbt_outer_main_sub_independent(self, dsp_radio: IcomRadio) -> None:
        """MAIN and SUB PBT Outer are independent."""
        await dsp_radio.set_pbt_outer(100, receiver=RECEIVER_MAIN)
        await dsp_radio.set_pbt_outer(200, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await dsp_radio.get_pbt_outer(receiver=RECEIVER_MAIN) == 100
        assert await dsp_radio.get_pbt_outer(receiver=RECEIVER_SUB) == 200

    async def test_pbt_outer_boundary_min(self, dsp_radio: IcomRadio) -> None:
        """PBT Outer 0 round-trips correctly."""
        await dsp_radio.set_pbt_outer(0, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_pbt_outer(receiver=RECEIVER_MAIN) == 0

    async def test_pbt_outer_boundary_max(self, dsp_radio: IcomRadio) -> None:
        """PBT Outer 255 round-trips correctly."""
        await dsp_radio.set_pbt_outer(255, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_pbt_outer(receiver=RECEIVER_MAIN) == 255

    async def test_pbt_outer_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited PBT Outer update; GET reflects new value."""
        dsp_mock._pbt_outer[RECEIVER_MAIN] = 190
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_LEVEL,
            _SUB_PBT_OUTER,
            data=_level_bcd_encode(190),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        assert await dsp_radio.get_pbt_outer(receiver=RECEIVER_MAIN) == 190


# ---------------------------------------------------------------------------
# 5. NB Level
# ---------------------------------------------------------------------------


class TestNbLevel:
    """NB Level (0x14/0x12, cmd29, per-receiver)."""

    async def test_nb_level_default(self, dsp_radio: IcomRadio) -> None:
        """Default NB level is 0."""
        assert await dsp_radio.get_nb_level(receiver=RECEIVER_MAIN) == 0

    async def test_nb_level_roundtrip_main(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set NB level 120 on MAIN, verify GET returns 120."""
        await dsp_radio.set_nb_level(120, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_nb_level(receiver=RECEIVER_MAIN)
        assert got == 120
        assert dsp_mock._nb_level[RECEIVER_MAIN] == 120

    async def test_nb_level_main_sub_independent(self, dsp_radio: IcomRadio) -> None:
        """MAIN and SUB NB levels are independent."""
        await dsp_radio.set_nb_level(80, receiver=RECEIVER_MAIN)
        await dsp_radio.set_nb_level(160, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await dsp_radio.get_nb_level(receiver=RECEIVER_MAIN) == 80
        assert await dsp_radio.get_nb_level(receiver=RECEIVER_SUB) == 160

    async def test_nb_level_boundary_min(self, dsp_radio: IcomRadio) -> None:
        """NB level 0 round-trips correctly."""
        await dsp_radio.set_nb_level(0, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_nb_level(receiver=RECEIVER_MAIN) == 0

    async def test_nb_level_boundary_max(self, dsp_radio: IcomRadio) -> None:
        """NB level 255 round-trips correctly."""
        await dsp_radio.set_nb_level(255, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_nb_level(receiver=RECEIVER_MAIN) == 255

    async def test_nb_level_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited NB level update; GET reflects new value."""
        dsp_mock._nb_level[RECEIVER_MAIN] = 180
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_LEVEL,
            _SUB_NB_LEVEL,
            data=_level_bcd_encode(180),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        assert await dsp_radio.get_nb_level(receiver=RECEIVER_MAIN) == 180


# ---------------------------------------------------------------------------
# 6. DIGI-SEL Shift
# ---------------------------------------------------------------------------


class TestDigiSelShift:
    """DIGI-SEL Shift (0x14/0x13, cmd29, per-receiver)."""

    async def test_digisel_shift_default(self, dsp_radio: IcomRadio) -> None:
        """Default DIGI-SEL shift is 128 (center)."""
        assert await dsp_radio.get_digisel_shift(receiver=RECEIVER_MAIN) == 128

    async def test_digisel_shift_roundtrip_main(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set DIGI-SEL shift 200 on MAIN, verify GET returns 200."""
        await dsp_radio.set_digisel_shift(200, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_digisel_shift(receiver=RECEIVER_MAIN)
        assert got == 200
        assert dsp_mock._digisel_shift[RECEIVER_MAIN] == 200

    async def test_digisel_shift_main_sub_independent(
        self, dsp_radio: IcomRadio
    ) -> None:
        """MAIN and SUB DIGI-SEL shifts are independent."""
        await dsp_radio.set_digisel_shift(50, receiver=RECEIVER_MAIN)
        await dsp_radio.set_digisel_shift(210, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await dsp_radio.get_digisel_shift(receiver=RECEIVER_MAIN) == 50
        assert await dsp_radio.get_digisel_shift(receiver=RECEIVER_SUB) == 210

    async def test_digisel_shift_boundary_min(self, dsp_radio: IcomRadio) -> None:
        """DIGI-SEL shift 0 round-trips correctly."""
        await dsp_radio.set_digisel_shift(0, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_digisel_shift(receiver=RECEIVER_MAIN) == 0

    async def test_digisel_shift_boundary_max(self, dsp_radio: IcomRadio) -> None:
        """DIGI-SEL shift 255 round-trips correctly."""
        await dsp_radio.set_digisel_shift(255, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_digisel_shift(receiver=RECEIVER_MAIN) == 255

    async def test_digisel_shift_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited DIGI-SEL shift update; GET reflects new value."""
        dsp_mock._digisel_shift[RECEIVER_MAIN] = 90
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_LEVEL,
            _SUB_DIGISEL_SHIFT,
            data=_level_bcd_encode(90),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        assert await dsp_radio.get_digisel_shift(receiver=RECEIVER_MAIN) == 90


# ---------------------------------------------------------------------------
# 7. AGC Time Constant
# ---------------------------------------------------------------------------


class TestAgcTimeConstant:
    """AGC Time Constant (0x1A/0x04, cmd29, per-receiver, BCD 0-13)."""

    async def test_agc_time_constant_default(self, dsp_radio: IcomRadio) -> None:
        """Default AGC time constant is 3."""
        val = await dsp_radio.get_agc_time_constant(receiver=RECEIVER_MAIN)
        assert val == 3

    async def test_agc_time_constant_roundtrip(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set AGC time constant 7, verify GET returns 7."""
        await dsp_radio.set_agc_time_constant(7, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_agc_time_constant(receiver=RECEIVER_MAIN)
        assert got == 7
        assert dsp_mock._agc_time_constant[RECEIVER_MAIN] == 7

    async def test_agc_time_constant_main_sub_independent(
        self, dsp_radio: IcomRadio
    ) -> None:
        """MAIN and SUB AGC time constants are independent."""
        await dsp_radio.set_agc_time_constant(5, receiver=RECEIVER_MAIN)
        await dsp_radio.set_agc_time_constant(10, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await dsp_radio.get_agc_time_constant(receiver=RECEIVER_MAIN) == 5
        assert await dsp_radio.get_agc_time_constant(receiver=RECEIVER_SUB) == 10

    async def test_agc_time_constant_boundary_min(self, dsp_radio: IcomRadio) -> None:
        """AGC time constant 0 round-trips correctly."""
        await dsp_radio.set_agc_time_constant(0, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_agc_time_constant(receiver=RECEIVER_MAIN) == 0

    async def test_agc_time_constant_boundary_max(self, dsp_radio: IcomRadio) -> None:
        """AGC time constant 13 round-trips correctly."""
        await dsp_radio.set_agc_time_constant(13, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_agc_time_constant(receiver=RECEIVER_MAIN) == 13

    async def test_agc_time_constant_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited AGC time constant update; GET reflects new value."""
        dsp_mock._agc_time_constant[RECEIVER_MAIN] = 9
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_CTL_MEM,
            _SUB_AGC_TIME_CONSTANT,
            data=bytes([_bcd_byte(9)]),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        assert await dsp_radio.get_agc_time_constant(receiver=RECEIVER_MAIN) == 9


# ---------------------------------------------------------------------------
# 8. Filter Shape
# ---------------------------------------------------------------------------


class TestFilterShape:
    """Filter Shape enum (0x16/0x56, cmd29, per-receiver): SHARP / SOFT."""

    async def test_filter_shape_default_sharp(self, dsp_radio: IcomRadio) -> None:
        """Default filter shape is SHARP."""
        shape = await dsp_radio.get_filter_shape(receiver=RECEIVER_MAIN)
        assert shape == FilterShape.SHARP

    async def test_filter_shape_roundtrip_soft(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set SOFT, verify GET returns SOFT."""
        await dsp_radio.set_filter_shape(FilterShape.SOFT, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_filter_shape(receiver=RECEIVER_MAIN)
        assert got == FilterShape.SOFT
        assert dsp_mock._filter_shape[RECEIVER_MAIN] == int(FilterShape.SOFT)

    async def test_filter_shape_roundtrip_sharp(self, dsp_radio: IcomRadio) -> None:
        """SOFT → SHARP round-trip works."""
        await dsp_radio.set_filter_shape(FilterShape.SOFT, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        await dsp_radio.set_filter_shape(FilterShape.SHARP, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert (
            await dsp_radio.get_filter_shape(receiver=RECEIVER_MAIN)
            == FilterShape.SHARP
        )

    async def test_filter_shape_main_sub_independent(
        self, dsp_radio: IcomRadio
    ) -> None:
        """MAIN and SUB filter shapes are independent."""
        await dsp_radio.set_filter_shape(FilterShape.SOFT, receiver=RECEIVER_MAIN)
        await dsp_radio.set_filter_shape(FilterShape.SHARP, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert (
            await dsp_radio.get_filter_shape(receiver=RECEIVER_MAIN) == FilterShape.SOFT
        )
        assert (
            await dsp_radio.get_filter_shape(receiver=RECEIVER_SUB) == FilterShape.SHARP
        )

    async def test_filter_shape_unsolicited_fires_event(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited filter shape change; filter_shape_changed fires."""
        events: list[tuple[str, dict]] = []
        dsp_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_PREAMP,
            _SUB_FILTER_SHAPE,
            data=bytes([_bcd_byte(int(FilterShape.SOFT))]),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "filter_shape_changed" for name, _ in events), (
            f"filter_shape_changed not fired; got {[n for n, _ in events]}"
        )
        matching = [d for n, d in events if n == "filter_shape_changed"]
        assert matching[0]["value"] == int(FilterShape.SOFT)


# ---------------------------------------------------------------------------
# 9. SSB TX Bandwidth
# ---------------------------------------------------------------------------


class TestSsbTxBandwidth:
    """SSB TX Bandwidth enum (0x16/0x58, global — no per-receiver)."""

    async def test_ssb_tx_bandwidth_default_wide(self, dsp_radio: IcomRadio) -> None:
        """Default SSB TX bandwidth is WIDE."""
        bw = await dsp_radio.get_ssb_tx_bandwidth()
        assert bw == SsbTxBandwidth.WIDE

    async def test_ssb_tx_bandwidth_roundtrip_mid(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Set MID, verify GET returns MID."""
        await dsp_radio.set_ssb_tx_bandwidth(SsbTxBandwidth.MID)
        await asyncio.sleep(_SETTLE)
        got = await dsp_radio.get_ssb_tx_bandwidth()
        assert got == SsbTxBandwidth.MID
        assert dsp_mock._ssb_tx_bandwidth == int(SsbTxBandwidth.MID)

    async def test_ssb_tx_bandwidth_cycle_all_values(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Cycle through all SSB TX bandwidth values: WIDE → MID → NAR → WIDE."""
        for target in (SsbTxBandwidth.MID, SsbTxBandwidth.NAR, SsbTxBandwidth.WIDE):
            await dsp_radio.set_ssb_tx_bandwidth(target)
            await asyncio.sleep(_SETTLE)
            got = await dsp_radio.get_ssb_tx_bandwidth()
            assert got == target, f"SSB TX BW: expected {target}, got {got}"
            assert dsp_mock._ssb_tx_bandwidth == int(target)

    async def test_ssb_tx_bandwidth_unsolicited_fires_event(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited SSB TX BW update; ssb_tx_bandwidth_changed fires."""
        events: list[tuple[str, dict]] = []
        dsp_radio._on_state_change = lambda name, data: events.append((name, data))  # type: ignore[assignment]

        frame = _build_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            _CMD_PREAMP,
            sub=_SUB_SSB_TX_BANDWIDTH,
            data=bytes([_bcd_byte(int(SsbTxBandwidth.NAR))]),
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)

        assert any(name == "ssb_tx_bandwidth_changed" for name, _ in events), (
            f"ssb_tx_bandwidth_changed not fired; got {[n for n, _ in events]}"
        )
        matching = [d for n, d in events if n == "ssb_tx_bandwidth_changed"]
        assert matching[0]["value"] == int(SsbTxBandwidth.NAR)


# ---------------------------------------------------------------------------
# 10. AF Mute
# ---------------------------------------------------------------------------


class TestAfMute:
    """AF Mute boolean (0x1A/0x09, cmd29, per-receiver)."""

    async def test_af_mute_default_off(self, dsp_radio: IcomRadio) -> None:
        """Default AF mute is off."""
        assert await dsp_radio.get_af_mute(receiver=RECEIVER_MAIN) is False

    async def test_af_mute_on_off_cycle(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """AF mute: off → on → off round-trip."""
        await dsp_radio.set_af_mute(True, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_af_mute(receiver=RECEIVER_MAIN) is True
        assert dsp_mock._af_mute[RECEIVER_MAIN] is True

        await dsp_radio.set_af_mute(False, receiver=RECEIVER_MAIN)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_af_mute(receiver=RECEIVER_MAIN) is False
        assert dsp_mock._af_mute[RECEIVER_MAIN] is False

    async def test_af_mute_main_sub_independent(self, dsp_radio: IcomRadio) -> None:
        """MAIN and SUB AF mute states are independent."""
        await dsp_radio.set_af_mute(True, receiver=RECEIVER_MAIN)
        await dsp_radio.set_af_mute(False, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)

        assert await dsp_radio.get_af_mute(receiver=RECEIVER_MAIN) is True
        assert await dsp_radio.get_af_mute(receiver=RECEIVER_SUB) is False

    async def test_af_mute_boundary_on(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """AF mute True encodes and decodes correctly."""
        await dsp_radio.set_af_mute(True, receiver=RECEIVER_SUB)
        await asyncio.sleep(_SETTLE)
        assert await dsp_radio.get_af_mute(receiver=RECEIVER_SUB) is True

    async def test_af_mute_unsolicited_frame(
        self, dsp_radio: IcomRadio, dsp_mock: DspLevelsMockRadio
    ) -> None:
        """Radio sends unsolicited AF mute update; GET reflects new state."""
        dsp_mock._af_mute[RECEIVER_MAIN] = True
        frame = _build_cmd29_civ(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            RECEIVER_MAIN,
            _CMD_CTL_MEM,
            _SUB_AF_MUTE,
            data=b"\x01",
        )
        dsp_mock.inject_unsolicited_civ(frame)
        await asyncio.sleep(0.15)
        assert await dsp_radio.get_af_mute(receiver=RECEIVER_MAIN) is True
