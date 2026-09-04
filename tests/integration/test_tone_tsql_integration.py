"""Mock-based integration tests for IC-7300 tone/TSQL commands (issue #134).

Tests the full request/response cycle for all 4 tone/TSQL command groups using a
local mock radio server.  No real hardware required — runs in CI without env vars.

Commands under test:
  1. Repeater Tone enable/disable (0x16 sub 0x42)
  2. Repeater TSQL enable/disable (0x16 sub 0x43)
  3. Tone Frequency set/get (0x1B sub 0x00)
  4. TSQL Frequency set/get (0x1B sub 0x01)

Uses IC-7300, not IC-7610 as originally written: MOR-2008 batch 2
(`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N)
migrated this family onto the bound command map, and `rigs/ic7610.toml`
declares the whole family absent (a live-bench readback found the old
hardcoded fallback's bytes decoded to garbage on this radio) -- so an
`IcomRadio` defaulting to IC-7610 now correctly raises `CommandError`
for every command this file exercises, instead of building a frame.
IC-7300 declares byte-identical `[0x16/0x1B, sub]` tuples for the family
and has no cmd29 routes at all (`rigs/ic7300.toml`: "single receiver, no
cmd29"), so every request here is plain, never cmd29-wrapped -- see
`ToneMockRadio`'s own docstring below for what that changed in the mock.

Run with::

    pytest tests/integration/test_tone_tsql_integration.py -v
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

_CMD_TONE = 0x1B
_CMD_FUNC = 0x16

_SUB_TONE_FREQ = 0x00
_SUB_TSQL_FREQ = 0x01
_SUB_REPEATER_TONE = 0x42
_SUB_REPEATER_TSQL = 0x43

_SETTLE = 0.05  # seconds: wait after fire-and-forget SET before GET

# Standard CTCSS test frequencies (exact centiHz)
_FREQ_MIN = 6700
_FREQ_DEFAULT = 8850
_FREQ_MID_LO = 11090
_FREQ_MID_HI = 13650
_FREQ_MID_2 = 16790
_FREQ_MAX = 25410


# ---------------------------------------------------------------------------
# BCD helpers
# ---------------------------------------------------------------------------


def _bcd_byte(value: int) -> int:
    """Encode 0-99 integer to one BCD byte (e.g. 18 → 0x18)."""
    return ((value // 10) << 4) | (value % 10)


def _bcd_byte_decode(b: int) -> int:
    """Decode one BCD byte to integer (e.g. 0x18 → 18)."""
    return ((b >> 4) & 0x0F) * 10 + (b & 0x0F)


def _encode_tone_freq(freq_centihz: int) -> bytes:
    """Encode an exact-centiHz CTCSS tone frequency to 3-byte BCD.

    MOR-2091: the radio packs six BCD digits as
    [0][0][100Hz digit][10Hz digit][1Hz digit][0.1Hz digit] -- not
    [hundreds][tens+units][tenths] (one byte per component) as this mock
    originally, incorrectly, assumed. See tests/test_tone_tsql.py's
    ``_BCD_TABLE`` header comment for the manual sourcing. E.g. 110.9 Hz:
    tenths=1109 -> byte[0]=0x00, byte[1]=_bcd_byte(11)=0x11,
    byte[2]=_bcd_byte(9)=0x09.
    """
    total_tenths = freq_centihz // 10
    return bytes([0x00, _bcd_byte(total_tenths // 100), _bcd_byte(total_tenths % 100)])


def _decode_tone_freq(data: bytes) -> int:
    """Decode 3-byte BCD to exact centiHz. Inverse of
    `_encode_tone_freq` above."""
    total_tenths = _bcd_byte_decode(data[1]) * 100 + _bcd_byte_decode(data[2])
    return total_tenths * 10


# ---------------------------------------------------------------------------
# Extended mock with tone/TSQL state
# ---------------------------------------------------------------------------


class ToneMockRadio(MockIcomRadio):
    """MockIcomRadio extended with repeater tone/TSQL and frequency state.

    Handles:
      - 0x16 / sub 0x42: repeater tone on/off
      - 0x16 / sub 0x43: repeater TSQL on/off
      - 0x1B / sub 0x00: tone frequency get/set (3-byte BCD)
      - 0x1B / sub 0x01: TSQL frequency get/set (3-byte BCD)

    All other commands are forwarded to the parent MockIcomRadio.

    Plain dispatch only, never cmd29: this file's radio is IC-7300
    (module docstring), which has no cmd29 routes at all, so every
    request the client builds is a bare ``FE FE <to> <from> <cmd> <sub>
    [data] FD`` frame -- there is no cmd29 envelope, and therefore no
    receiver-selector byte anywhere in the payload (that byte only
    exists inside a cmd29 wrapper, which the base class's own
    ``_dispatch_civ``/``_dispatch_cmd29`` split already strips before a
    subclass ever sees it -- see ``_dispatch_cmd29``'s ``receiver``
    parameter, passed in separately, never embedded in ``inner``).
    Earlier revisions of this mock (written against IC-7610, which does
    use cmd29 for this whole family) had a ``_strip_receiver_prefix``
    step in the plain-path handlers below that assumed a receiver byte
    was there to strip; it was never exercised while every real request
    on IC-7610 went through ``_dispatch_cmd29`` instead, and would have
    corrupted the BCD frequency payload the day it was (the hundreds
    digit of any frequency under 200 Hz is legitimately ``0x00`` or
    ``0x01``, indistinguishable from a receiver-selector byte). Removed
    rather than fixed forward, since a plain frame never carries one.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._repeater_tone: int = 0  # 0 = off, 1 = on
        self._repeater_tsql: int = 0  # 0 = off, 1 = on
        self._tone_freq_centihz: int = _FREQ_DEFAULT
        self._tsql_freq_centihz: int = _FREQ_DEFAULT

    # ------------------------------------------------------------------
    # CI-V dispatch override
    # ------------------------------------------------------------------

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        """Intercept repeater tone/TSQL (0x16) and tone frequency (0x1B)
        commands -- both always plain here (IC-7300 has no cmd29 route for
        either), so there is no separate cmd29 dispatch override in this
        class any more (see the class docstring)."""
        if cmd == _CMD_FUNC:
            return self._dispatch_func(payload, from_addr)
        if cmd == _CMD_TONE:
            return self._dispatch_tone(payload, from_addr)
        return super()._dispatch_civ(cmd, payload, from_addr)

    # ------------------------------------------------------------------
    # Repeater tone/TSQL dispatch (0x16)
    # ------------------------------------------------------------------

    def _dispatch_func(self, payload: bytes, from_addr: int) -> bytes | None:
        """Dispatch repeater tone/TSQL commands (cmd 0x16)."""
        if not payload:
            return self._civ_nak(from_addr, self._radio_addr)
        sub = payload[0]
        rest = payload[1:]
        if sub == _SUB_REPEATER_TONE:
            return self._handle_repeater_tone(rest, from_addr)
        if sub == _SUB_REPEATER_TSQL:
            return self._handle_repeater_tsql(rest, from_addr)
        # Not a tone/TSQL sub-command: let the parent handle other 0x16
        # sub-commands (ATT, preamp status, etc.)
        return super()._dispatch_civ(_CMD_FUNC, payload, from_addr)

    # ------------------------------------------------------------------
    # Tone frequency dispatch (0x1B)
    # ------------------------------------------------------------------

    def _dispatch_tone(self, payload: bytes, from_addr: int) -> bytes | None:
        """Dispatch tone-frequency commands (cmd 0x1B)."""
        if not payload:
            return self._civ_nak(from_addr, self._radio_addr)
        sub = payload[0]
        rest = payload[1:]
        if sub == _SUB_TONE_FREQ:
            return self._handle_tone_freq(rest, from_addr)
        if sub == _SUB_TSQL_FREQ:
            return self._handle_tsql_freq(rest, from_addr)
        return self._civ_nak(from_addr, self._radio_addr)

    # ------------------------------------------------------------------
    # Individual command handlers
    #
    # A plain frame (no cmd29 wrapper) never carries a receiver-selector
    # byte -- that byte lives only in the cmd29 envelope itself, which
    # this mock never receives for this radio (class docstring). `rest`
    # below is exactly the on/off byte (repeater tone/TSQL) or the 3-byte
    # BCD payload (tone/TSQL frequency), with nothing to strip first.
    # ------------------------------------------------------------------

    def _handle_repeater_tone(self, rest: bytes, from_addr: int) -> bytes:
        """Handle repeater tone enable/disable (0x16 sub 0x42)."""
        to = from_addr
        frm = self._radio_addr
        if rest:  # SET
            self._repeater_tone = rest[0]
            return self._civ_ack(to, frm)
        return self._civ_frame(
            to,
            frm,
            _CMD_FUNC,
            sub=_SUB_REPEATER_TONE,
            data=bytes([self._repeater_tone]),
        )

    def _handle_repeater_tsql(self, rest: bytes, from_addr: int) -> bytes:
        """Handle repeater TSQL enable/disable (0x16 sub 0x43)."""
        to = from_addr
        frm = self._radio_addr
        if rest:  # SET
            self._repeater_tsql = rest[0]
            return self._civ_ack(to, frm)
        return self._civ_frame(
            to,
            frm,
            _CMD_FUNC,
            sub=_SUB_REPEATER_TSQL,
            data=bytes([self._repeater_tsql]),
        )

    def _handle_tone_freq(self, rest: bytes, from_addr: int) -> bytes:
        """Handle tone frequency get/set (0x1B sub 0x00)."""
        to = from_addr
        frm = self._radio_addr
        if len(rest) >= 3:  # SET (3-byte BCD payload)
            self._tone_freq_centihz = _decode_tone_freq(rest[:3])
            return self._civ_ack(to, frm)
        return self._civ_frame(
            to,
            frm,
            _CMD_TONE,
            sub=_SUB_TONE_FREQ,
            data=_encode_tone_freq(self._tone_freq_centihz),
        )

    def _handle_tsql_freq(self, rest: bytes, from_addr: int) -> bytes:
        """Handle TSQL frequency get/set (0x1B sub 0x01)."""
        to = from_addr
        frm = self._radio_addr
        if len(rest) >= 3:  # SET (3-byte BCD payload)
            self._tsql_freq_centihz = _decode_tone_freq(rest[:3])
            return self._civ_ack(to, frm)
        return self._civ_frame(
            to,
            frm,
            _CMD_TONE,
            sub=_SUB_TSQL_FREQ,
            data=_encode_tone_freq(self._tsql_freq_centihz),
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# IC-7300's CI-V address (RadioModel(name="IC-7300", civ_addr=148)) --
# module docstring explains why this file uses IC-7300, not the mock's
# own IC-7610 default (MockIcomRadio's own radio_addr default, 0x98).
_IC_7300_ADDR = 0x94


@pytest.fixture
async def tone_mock() -> AsyncGenerator[ToneMockRadio, None]:
    """Start a ToneMockRadio server for each test, stop it after."""
    server = ToneMockRadio(radio_addr=_IC_7300_ADDR)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def tone_radio(tone_mock: ToneMockRadio) -> AsyncGenerator[IcomRadio, None]:
    """IcomRadio connected to ToneMockRadio, disconnected after each test."""
    radio = IcomRadio(
        host="127.0.0.1",
        port=tone_mock.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7300",
    )
    with fast_connect():
        await radio.connect()
    yield radio
    await radio.disconnect()


# ---------------------------------------------------------------------------
# 1. Repeater Tone
# ---------------------------------------------------------------------------


class TestRepeaterTone:
    """Repeater tone enable/disable roundtrip tests."""

    async def test_default_off(self, tone_radio: IcomRadio) -> None:
        """Default repeater tone state is OFF."""
        result = await tone_radio.get_repeater_tone()
        assert result is False

    async def test_set_on(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set repeater tone ON and verify via GET."""
        await tone_radio.set_repeater_tone(True)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_repeater_tone()
        assert result is True
        assert tone_mock._repeater_tone == 1

    async def test_set_off_after_on(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set repeater tone ON then OFF; GET returns False."""
        await tone_radio.set_repeater_tone(True)
        await asyncio.sleep(_SETTLE)

        await tone_radio.set_repeater_tone(False)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_repeater_tone()
        assert result is False
        assert tone_mock._repeater_tone == 0

    async def test_toggle_on_off_on(self, tone_radio: IcomRadio) -> None:
        """Toggle repeater tone on → off → on."""
        await tone_radio.set_repeater_tone(True)
        await asyncio.sleep(_SETTLE)
        assert await tone_radio.get_repeater_tone() is True

        await tone_radio.set_repeater_tone(False)
        await asyncio.sleep(_SETTLE)
        assert await tone_radio.get_repeater_tone() is False

        await tone_radio.set_repeater_tone(True)
        await asyncio.sleep(_SETTLE)
        assert await tone_radio.get_repeater_tone() is True


# ---------------------------------------------------------------------------
# 2. Repeater TSQL
# ---------------------------------------------------------------------------


class TestRepeaterTSQL:
    """Repeater TSQL enable/disable roundtrip tests."""

    async def test_default_off(self, tone_radio: IcomRadio) -> None:
        """Default repeater TSQL state is OFF."""
        result = await tone_radio.get_repeater_tsql()
        assert result is False

    async def test_set_on(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set repeater TSQL ON and verify via GET."""
        await tone_radio.set_repeater_tsql(True)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_repeater_tsql()
        assert result is True
        assert tone_mock._repeater_tsql == 1

    async def test_set_off_after_on(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set repeater TSQL ON then OFF; GET returns False."""
        await tone_radio.set_repeater_tsql(True)
        await asyncio.sleep(_SETTLE)

        await tone_radio.set_repeater_tsql(False)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_repeater_tsql()
        assert result is False
        assert tone_mock._repeater_tsql == 0

    async def test_toggle_on_off_on(self, tone_radio: IcomRadio) -> None:
        """Toggle repeater TSQL on → off → on."""
        await tone_radio.set_repeater_tsql(True)
        await asyncio.sleep(_SETTLE)
        assert await tone_radio.get_repeater_tsql() is True

        await tone_radio.set_repeater_tsql(False)
        await asyncio.sleep(_SETTLE)
        assert await tone_radio.get_repeater_tsql() is False

        await tone_radio.set_repeater_tsql(True)
        await asyncio.sleep(_SETTLE)
        assert await tone_radio.get_repeater_tsql() is True

    async def test_tone_and_tsql_independent(self, tone_radio: IcomRadio) -> None:
        """Repeater tone and TSQL are independent flags."""
        await tone_radio.set_repeater_tone(True)
        await tone_radio.set_repeater_tsql(False)
        await asyncio.sleep(_SETTLE)

        assert await tone_radio.get_repeater_tone() is True
        assert await tone_radio.get_repeater_tsql() is False

        await tone_radio.set_repeater_tone(False)
        await tone_radio.set_repeater_tsql(True)
        await asyncio.sleep(_SETTLE)

        assert await tone_radio.get_repeater_tone() is False
        assert await tone_radio.get_repeater_tsql() is True


# ---------------------------------------------------------------------------
# 3. Tone Frequency
# ---------------------------------------------------------------------------


class TestToneFrequency:
    """Tone frequency set/get roundtrip tests."""

    async def test_default_freq(self, tone_radio: IcomRadio) -> None:
        """Default tone frequency is 88.5 Hz."""
        result = await tone_radio.get_tone_freq()
        assert result == _FREQ_DEFAULT

    async def test_set_min_freq(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set tone frequency to 67.0 Hz (minimum CTCSS tone)."""
        await tone_radio.set_tone_freq(_FREQ_MIN)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_tone_freq()
        assert result == _FREQ_MIN
        assert tone_mock._tone_freq_centihz == _FREQ_MIN

    async def test_set_110_9_hz(self, tone_radio: IcomRadio) -> None:
        """Set tone frequency to 110.9 Hz."""
        await tone_radio.set_tone_freq(_FREQ_MID_LO)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_tone_freq()
        assert result == _FREQ_MID_LO

    async def test_set_max_freq(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set tone frequency to 254.1 Hz (maximum CTCSS tone)."""
        await tone_radio.set_tone_freq(_FREQ_MAX)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_tone_freq()
        assert result == _FREQ_MAX
        assert tone_mock._tone_freq_centihz == _FREQ_MAX

    async def test_multiple_freq_changes(self, tone_radio: IcomRadio) -> None:
        """Change tone frequency through all standard test values."""
        for freq in (
            _FREQ_MIN,
            _FREQ_DEFAULT,
            _FREQ_MID_LO,
            _FREQ_MID_HI,
            _FREQ_MID_2,
            _FREQ_MAX,
        ):
            await tone_radio.set_tone_freq(freq)
            await asyncio.sleep(_SETTLE)
            result = await tone_radio.get_tone_freq()
            assert result == freq, f"Expected {freq} centiHz, got {result} centiHz"

    async def test_freq_roundtrip_precision(self, tone_radio: IcomRadio) -> None:
        """BCD encoding/decoding preserves single-decimal precision."""
        for freq in (6700, 7700, 8850, 10000, 11090, 12730, 16790, 20350, 25410):
            await tone_radio.set_tone_freq(freq)
            await asyncio.sleep(_SETTLE)
            result = await tone_radio.get_tone_freq()
            assert result == freq, f"Precision loss: sent {freq}, got {result}"


# ---------------------------------------------------------------------------
# 4. TSQL Frequency
# ---------------------------------------------------------------------------


class TestTSQLFrequency:
    """TSQL frequency set/get roundtrip tests."""

    async def test_default_freq(self, tone_radio: IcomRadio) -> None:
        """Default TSQL frequency is 88.5 Hz."""
        result = await tone_radio.get_tsql_freq()
        assert result == _FREQ_DEFAULT

    async def test_set_min_freq(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set TSQL frequency to 67.0 Hz (minimum CTCSS tone)."""
        await tone_radio.set_tsql_freq(_FREQ_MIN)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_tsql_freq()
        assert result == _FREQ_MIN
        assert tone_mock._tsql_freq_centihz == _FREQ_MIN

    async def test_set_110_9_hz(self, tone_radio: IcomRadio) -> None:
        """Set TSQL frequency to 110.9 Hz."""
        await tone_radio.set_tsql_freq(_FREQ_MID_LO)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_tsql_freq()
        assert result == _FREQ_MID_LO

    async def test_set_max_freq(
        self, tone_radio: IcomRadio, tone_mock: ToneMockRadio
    ) -> None:
        """Set TSQL frequency to 254.1 Hz (maximum CTCSS tone)."""
        await tone_radio.set_tsql_freq(_FREQ_MAX)
        await asyncio.sleep(_SETTLE)

        result = await tone_radio.get_tsql_freq()
        assert result == _FREQ_MAX
        assert tone_mock._tsql_freq_centihz == _FREQ_MAX

    async def test_multiple_freq_changes(self, tone_radio: IcomRadio) -> None:
        """Change TSQL frequency through all standard test values."""
        for freq in (
            _FREQ_MIN,
            _FREQ_DEFAULT,
            _FREQ_MID_LO,
            _FREQ_MID_HI,
            _FREQ_MID_2,
            _FREQ_MAX,
        ):
            await tone_radio.set_tsql_freq(freq)
            await asyncio.sleep(_SETTLE)
            result = await tone_radio.get_tsql_freq()
            assert result == freq, f"Expected {freq} centiHz, got {result} centiHz"

    async def test_tone_and_tsql_freq_independent(self, tone_radio: IcomRadio) -> None:
        """Tone and TSQL frequencies are stored independently."""
        await tone_radio.set_tone_freq(_FREQ_MID_LO)
        await tone_radio.set_tsql_freq(_FREQ_MAX)
        await asyncio.sleep(_SETTLE)

        tone = await tone_radio.get_tone_freq()
        tsql = await tone_radio.get_tsql_freq()
        assert tone == _FREQ_MID_LO
        assert tsql == _FREQ_MAX

    async def test_freq_roundtrip_precision(self, tone_radio: IcomRadio) -> None:
        """BCD encoding/decoding preserves single-decimal precision for TSQL."""
        for freq in (6700, 7700, 8850, 10000, 11090, 12730, 16790, 20350, 25410):
            await tone_radio.set_tsql_freq(freq)
            await asyncio.sleep(_SETTLE)
            result = await tone_radio.get_tsql_freq()
            assert result == freq, f"Precision loss: sent {freq}, got {result}"


# ---------------------------------------------------------------------------
# BCD codec unit checks (no radio needed)
# ---------------------------------------------------------------------------


class TestBcdCodec:
    """Verify the mock's BCD encode/decode helpers are self-consistent."""

    @pytest.mark.parametrize(
        "freq",
        [
            6700,
            7700,
            8850,
            10000,
            11090,
            12730,
            13650,
            16790,
            20350,
            25410,
        ],
    )
    def test_encode_decode_roundtrip(self, freq: int) -> None:
        """encode → decode must recover the original frequency."""
        encoded = _encode_tone_freq(freq)
        assert len(encoded) == 3
        decoded = _decode_tone_freq(encoded)
        assert decoded == freq, f"Roundtrip failed for {freq} centiHz: got {decoded}"
