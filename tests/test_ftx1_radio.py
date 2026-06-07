"""Unit tests for YaesuCatRadio (mock transport — no real hardware required)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, call

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.parser import CatParseError
from rigplane.backends.yaesu_cat.transport import CatTimeoutError
from rigplane.exceptions import CommandError
from rigplane.exceptions import ConnectionError as RadioConnectionError
from rigplane.rig_loader import load_rig

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RIGS_DIR = Path(__file__).parents[1] / "rigs"


@pytest.fixture()
def config():
    """Load the real ftx1 RigConfig from TOML."""
    return load_rig(_RIGS_DIR / "ftx1.toml")


@pytest.fixture()
def radio(config):
    """Return a YaesuCatRadio with mocked transport (not connected)."""
    r = YaesuCatRadio("/dev/null", profile=config)
    return r


@pytest.fixture()
def connected_radio(radio):
    """Return a YaesuCatRadio whose transport reports connected=True."""
    radio._transport._connected = True
    return radio


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


def test_instantiation_with_string_profile():
    """Can create a radio using the 'ftx1' profile string."""
    r = YaesuCatRadio("/dev/null", profile="ftx1")
    assert r.model == "FTX-1"
    assert not r.connected


def test_instantiation_with_rig_config(config):
    """Can create a radio using an already-loaded RigConfig."""
    r = YaesuCatRadio("/dev/null", profile=config)
    assert r.model == "FTX-1"


def test_capabilities_include_expected(config):
    r = YaesuCatRadio("/dev/null", profile=config)
    assert "tx" in r.capabilities
    assert "meters" in r.capabilities


def test_mode_map_built_correctly(radio):
    """Mode codes are 1-based; LSB=1, USB=2, CW-U=3."""
    assert radio._code_to_mode["1"] == "LSB"
    assert radio._code_to_mode["2"] == "USB"
    assert radio._code_to_mode["3"] == "CW-U"
    assert radio._mode_to_code["LSB"] == "1"
    assert radio._mode_to_code["USB"] == "2"


def test_parsers_compiled_for_key_commands(radio):
    """Parser cache should have entries for the four core commands."""
    assert "get_freq" in radio._parsers
    assert "get_mode" in radio._parsers
    assert "get_ptt" in radio._parsers
    assert "get_s_meter" in radio._parsers


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_manager_connects_and_disconnects(radio):
    """__aenter__ calls connect(), __aexit__ calls disconnect()."""
    radio._transport.connect = AsyncMock()
    radio._transport.close = AsyncMock()

    async with radio as r:
        assert r is radio
        radio._transport.connect.assert_called_once()

    radio._transport.close.assert_called_once()


@pytest.mark.asyncio
async def test_require_connected_raises_when_not_connected(radio):
    with pytest.raises(RadioConnectionError):
        await radio.get_freq()


# ---------------------------------------------------------------------------
# get_freq / set_freq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_freq_main(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="FA014074000")
    freq = await connected_radio.get_freq(receiver=0)
    assert freq == 14_074_000
    connected_radio._transport.query.assert_called_once_with("FA;")
    assert connected_radio.radio_state.main.freq == 14_074_000


@pytest.mark.asyncio
async def test_get_freq_sub(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="FB007074000")
    freq = await connected_radio.get_freq(receiver=1)
    assert freq == 7_074_000
    connected_radio._transport.query.assert_called_once_with("FB;")
    assert connected_radio.radio_state.sub.freq == 7_074_000


@pytest.mark.asyncio
async def test_set_freq_main(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_freq(14_074_000, receiver=0)
    connected_radio._transport.write.assert_called_once_with("FA014074000;")
    assert connected_radio.radio_state.main.freq == 14_074_000


@pytest.mark.asyncio
async def test_set_freq_sub(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_freq(7_074_000, receiver=1)
    connected_radio._transport.write.assert_called_once_with("FB007074000;")
    assert connected_radio.radio_state.sub.freq == 7_074_000


@pytest.mark.asyncio
async def test_get_freq_roundtrip(connected_radio):
    """set_freq then get_freq returns same value (via mock)."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._transport.query = AsyncMock(return_value="FA021074000")

    await connected_radio.set_freq(21_074_000)
    freq = await connected_radio.get_freq()
    assert freq == 21_074_000


# ---------------------------------------------------------------------------
# get_mode / set_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mode_usb(connected_radio):
    # Code "2" → USB
    connected_radio._transport.query = AsyncMock(return_value="MD02")
    mode, filt = await connected_radio.get_mode(receiver=0)
    assert mode == "USB"
    assert filt is None
    assert connected_radio.radio_state.main.mode == "USB"


@pytest.mark.asyncio
async def test_get_mode_lsb(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="MD01")
    mode, _ = await connected_radio.get_mode()
    assert mode == "LSB"


@pytest.mark.asyncio
async def test_get_mode_sub_receiver(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="MD14")
    mode, _ = await connected_radio.get_mode(receiver=1)
    assert mode == "FM"
    connected_radio._transport.query.assert_called_once_with("MD1;")
    assert connected_radio.radio_state.sub.mode == "FM"


@pytest.mark.asyncio
async def test_set_mode_usb(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_mode("USB", receiver=0)
    connected_radio._transport.write.assert_called_once_with("MD02;")
    assert connected_radio.radio_state.main.mode == "USB"


@pytest.mark.asyncio
async def test_set_mode_sub(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_mode("LSB", receiver=1)
    connected_radio._transport.write.assert_called_once_with("MD11;")
    assert connected_radio.radio_state.sub.mode == "LSB"


@pytest.mark.asyncio
async def test_set_mode_unknown_raises(connected_radio):
    with pytest.raises(CommandError, match="Unknown mode"):
        await connected_radio.set_mode("INVALID_MODE")


@pytest.mark.asyncio
async def test_get_set_mode_roundtrip(connected_radio):
    """set_mode("CW-U") then get_mode returns "CW-U"."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._transport.query = AsyncMock(return_value="MD03")

    await connected_radio.set_mode("CW-U")
    mode, _ = await connected_radio.get_mode()
    assert mode == "CW-U"


def test_mode_map_hex_codes(radio):
    """MOR-473: Yaesu MD codes are HEX nibbles, so codes 10-15 are A-F.

    The decimal map silently broke every mode with index >= 10
    (DATA-FM/FM-N/DATA-U/AM-N/PSK/DATA-FM-N). Codes 1-9 are unchanged
    (hex == dec).
    """
    assert radio._code_to_mode["A"] == "DATA-FM"
    assert radio._code_to_mode["B"] == "FM-N"
    assert radio._code_to_mode["C"] == "DATA-U"
    assert radio._code_to_mode["D"] == "AM-N"
    assert radio._code_to_mode["E"] == "PSK"
    assert radio._code_to_mode["F"] == "DATA-FM-N"
    assert radio._mode_to_code["DATA-FM"] == "A"
    assert radio._mode_to_code["DATA-U"] == "C"
    assert radio._mode_to_code["AM-N"] == "D"
    assert radio._mode_to_code["PSK"] == "E"
    assert radio._mode_to_code["DATA-FM-N"] == "F"


@pytest.mark.asyncio
async def test_get_mode_data_u(connected_radio):
    """MOR-473: ``MD0C;`` (hex C = 12) decodes to DATA-U, not UNKNOWN(C)."""
    connected_radio._transport.query = AsyncMock(return_value="MD0C")
    mode, _ = await connected_radio.get_mode(receiver=0)
    assert mode == "DATA-U"
    assert connected_radio.radio_state.main.mode == "DATA-U"


@pytest.mark.asyncio
async def test_set_mode_data_u(connected_radio):
    """MOR-473: set_mode("DATA-U") writes the hex code ``MD0C;``."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_mode("DATA-U", receiver=0)
    connected_radio._transport.write.assert_called_once_with("MD0C;")
    assert connected_radio.radio_state.main.mode == "DATA-U"


# ---------------------------------------------------------------------------
# Power switch (PS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_powerstat_on(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="PS1")
    assert await connected_radio.get_powerstat() is True


@pytest.mark.asyncio
async def test_get_powerstat_off(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="PS0")
    assert await connected_radio.get_powerstat() is False


@pytest.mark.asyncio
async def test_set_powerstat_on(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_powerstat(True)
    connected_radio._transport.write.assert_called_once_with("PS1;")


@pytest.mark.asyncio
async def test_set_powerstat_off(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_powerstat(False)
    connected_radio._transport.write.assert_called_once_with("PS0;")


# ---------------------------------------------------------------------------
# PTT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_ptt_on(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_ptt(True)
    connected_radio._transport.write.assert_called_once_with("TX1;")
    assert connected_radio.radio_state.ptt is True


@pytest.mark.asyncio
async def test_set_ptt_off(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_ptt(False)
    connected_radio._transport.write.assert_called_once_with("TX0;")
    assert connected_radio.radio_state.ptt is False


@pytest.mark.asyncio
async def test_get_ptt_transmitting(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="TX1")
    ptt = await connected_radio.get_ptt()
    assert ptt is True
    assert connected_radio.radio_state.ptt is True


@pytest.mark.asyncio
async def test_get_ptt_receiving(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="TX0")
    ptt = await connected_radio.get_ptt()
    assert ptt is False


# ---------------------------------------------------------------------------
# S-meter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_s_meter_main(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="SM0130")
    raw = await connected_radio.get_s_meter(receiver=0)
    assert raw == 130
    connected_radio._transport.query.assert_called_once_with("SM0;")
    assert connected_radio.radio_state.main.s_meter == 130


@pytest.mark.asyncio
async def test_get_s_meter_sub(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="SM1055")
    raw = await connected_radio.get_s_meter(receiver=1)
    assert raw == 55
    connected_radio._transport.query.assert_called_once_with("SM1;")
    assert connected_radio.radio_state.sub.s_meter == 55


@pytest.mark.asyncio
async def test_get_s_meter_zero(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="SM0000")
    raw = await connected_radio.get_s_meter()
    assert raw == 0


@pytest.mark.asyncio
async def test_get_s_meter_max(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="SM0255")
    raw = await connected_radio.get_s_meter()
    assert raw == 255


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_not_in_profile_raises(connected_radio):
    """Accessing a command not in the profile raises CommandError."""
    with pytest.raises(CommandError, match="not found in profile"):
        connected_radio._get_spec("nonexistent_command_xyz")


@pytest.mark.asyncio
async def test_transport_timeout_propagates(connected_radio):
    """CatTimeoutError from transport bubbles up unchanged."""
    connected_radio._transport.query = AsyncMock(side_effect=CatTimeoutError("timeout"))
    with pytest.raises(CatTimeoutError):
        await connected_radio.get_freq()


@pytest.mark.asyncio
async def test_parse_error_propagates(connected_radio):
    """Malformed response raises CatParseError."""
    connected_radio._transport.query = AsyncMock(return_value="GARBAGE_RESPONSE")
    with pytest.raises(CatParseError):
        await connected_radio.get_freq()


def test_radio_state_initially_default(radio):
    """radio_state is a RadioState with default values."""
    from rigplane.radio_state import RadioState

    assert isinstance(radio.radio_state, RadioState)
    assert radio.radio_state.ptt is False
    assert radio.radio_state.main.freq == 0


# ---------------------------------------------------------------------------
# D1: RX Audio Controls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_af_level(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="AG0128")
    level = await connected_radio.get_af_level()
    assert level == 128
    connected_radio._transport.query.assert_called_once_with("AG0;")


@pytest.mark.asyncio
async def test_set_af_level(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_af_level(200)
    connected_radio._transport.write.assert_called_once_with("AG0200;")


@pytest.mark.asyncio
async def test_get_rf_gain(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RG0255")
    assert await connected_radio.get_rf_gain() == 255
    connected_radio._transport.query.assert_called_once_with("RG0;")


@pytest.mark.asyncio
async def test_set_squelch(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_squelch(50)
    connected_radio._transport.write.assert_called_once_with("SQ0050;")


# ---------------------------------------------------------------------------
# D2: RF Front-End
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_attenuator_off(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RA00")
    state = await connected_radio.get_attenuator()
    assert state is False
    connected_radio._transport.query.assert_called_once_with("RA0;")


@pytest.mark.asyncio
async def test_get_attenuator_on(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RA01")
    state = await connected_radio.get_attenuator()
    assert state is True
    connected_radio._transport.query.assert_called_once_with("RA0;")


@pytest.mark.asyncio
async def test_set_attenuator_on(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_attenuator(1)
    connected_radio._transport.write.assert_called_once_with("RA01;")


@pytest.mark.asyncio
async def test_set_attenuator_off(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_attenuator(0)
    connected_radio._transport.write.assert_called_once_with("RA00;")


@pytest.mark.asyncio
async def test_set_attenuator_bool_true_coerced_to_int(connected_radio):
    """MOR-498: callers/validator pass a Python bool; ``str(True)`` would
    render the malformed ``RA0True;`` and the radio would silently ignore it.
    The bool must be coerced to int so the CAT write is ``RA01;``."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_attenuator(True)
    connected_radio._transport.write.assert_called_once_with("RA01;")


@pytest.mark.asyncio
async def test_set_attenuator_bool_false_coerced_to_int(connected_radio):
    """MOR-498: ``set_attenuator(False)`` must render ``RA00;``, not
    ``RA0False;``."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_attenuator(False)
    connected_radio._transport.write.assert_called_once_with("RA00;")


@pytest.mark.asyncio
async def test_get_preamp(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="PA01")
    assert await connected_radio.get_preamp() == 1
    connected_radio._transport.query.assert_called_once_with("PA0;")


@pytest.mark.asyncio
async def test_set_preamp(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_preamp(2)
    connected_radio._transport.write.assert_called_once_with("PA02;")


# ---------------------------------------------------------------------------
# D3: DSP (NB/NR/Notch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_nb_level(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="NL0005")
    assert await connected_radio.get_nb_level() == 5
    connected_radio._transport.query.assert_called_once_with("NL0;")


@pytest.mark.asyncio
async def test_set_nb_level(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_nb_level(3)
    connected_radio._transport.write.assert_called_once_with("NL0003;")


@pytest.mark.asyncio
async def test_get_nr_level(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RL007")
    assert await connected_radio.get_nr_level() == 7
    connected_radio._transport.query.assert_called_once_with("RL0;")


@pytest.mark.asyncio
async def test_get_auto_notch_on(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="BC01")
    assert await connected_radio.get_auto_notch() is True


@pytest.mark.asyncio
async def test_get_auto_notch_off(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="BC00")
    assert await connected_radio.get_auto_notch() is False


@pytest.mark.asyncio
async def test_set_auto_notch(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_auto_notch(True)
    connected_radio._transport.write.assert_called_once_with("BC01;")


@pytest.mark.asyncio
async def test_get_manual_notch(connected_radio):
    """get_manual_notch calls both BP00 and BP01 queries."""
    responses = iter(["BP00001", "BP01120"])
    connected_radio._transport.query = AsyncMock(side_effect=responses)
    enabled, freq = await connected_radio.get_manual_notch()
    assert enabled is True
    assert freq == 120


@pytest.mark.asyncio
async def test_set_manual_notch(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_manual_notch(True)
    connected_radio._transport.write.assert_called_once_with("BP00001;")


@pytest.mark.asyncio
async def test_set_manual_notch_freq(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_manual_notch_freq(80)
    connected_radio._transport.write.assert_called_once_with("BP01080;")


@pytest.mark.asyncio
async def test_set_notch_filter_delegates_to_manual_notch_freq(connected_radio):
    """Cross-vendor set_notch_filter alias forwards to BP01 (issue #1102)."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_notch_filter(80)
    connected_radio._transport.write.assert_called_once_with("BP01080;")


@pytest.mark.asyncio
async def test_get_notch_filter_returns_freq_index(connected_radio):
    """Cross-vendor get_notch_filter alias returns BP01 freq index (issue #1102)."""
    responses = iter(["BP00001", "BP01120"])
    connected_radio._transport.query = AsyncMock(side_effect=responses)
    assert await connected_radio.get_notch_filter() == 120


# ---------------------------------------------------------------------------
# D4: Filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_filter_width(connected_radio):
    """get_filter_width returns Hz (issue #1101): index 10 → 2100 Hz on USB."""

    # The width table is resolved from the radio's CURRENT mode, read fresh via
    # CAT (MOR-507): get_filter_width issues a mode query (MD0;) then the
    # filter query (SH0;). Mode code "2" → USB; USB table index 10 → 2100 Hz.
    async def fake_query(cmd: str) -> str:
        return "MD02" if cmd.startswith("MD") else "SH0010"

    connected_radio._transport.query = AsyncMock(side_effect=fake_query)
    assert await connected_radio.get_filter_width() == 2100
    connected_radio._transport.query.assert_any_call("SH0;")
    connected_radio._transport.query.assert_any_call("MD0;")


@pytest.mark.asyncio
async def test_set_filter_width(connected_radio):
    """set_filter_width takes Hz (issue #1101): 1200 Hz → index 6 on USB."""
    connected_radio._transport.write = AsyncMock()
    # Default mode is "USB"; USB table index 6 → 1500 Hz; index 12 → 2400 Hz.
    # Use 2400 Hz as it lands exactly on a table entry.
    await connected_radio.set_filter_width(2400)
    connected_radio._transport.write.assert_called_once_with("SH0012;")


@pytest.mark.asyncio
async def test_get_if_shift_positive(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="IS00+0500")
    offset = await connected_radio.get_if_shift()
    assert offset == 500


@pytest.mark.asyncio
async def test_get_if_shift_negative(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="IS00-0200")
    offset = await connected_radio.get_if_shift()
    assert offset == -200


@pytest.mark.asyncio
async def test_set_if_shift_positive(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_if_shift(300)
    connected_radio._transport.write.assert_called_once_with("IS00+0300;")


@pytest.mark.asyncio
async def test_set_if_shift_negative(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_if_shift(-150)
    connected_radio._transport.write.assert_called_once_with("IS00-0150;")


@pytest.mark.asyncio
async def test_get_narrow(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="NA01")
    assert await connected_radio.get_narrow() is True


@pytest.mark.asyncio
async def test_set_narrow(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_narrow(False)
    connected_radio._transport.write.assert_called_once_with("NA00;")


# ---------------------------------------------------------------------------
# D5: Split/Dual Watch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_split_on(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="ST1")
    assert await connected_radio.get_split() is True
    connected_radio._transport.query.assert_called_once_with("ST;")


@pytest.mark.asyncio
async def test_set_split(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_split(True)
    connected_radio._transport.write.assert_called_once_with("ST1;")


@pytest.mark.asyncio
async def test_get_rx_func(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="FR00")
    assert await connected_radio.get_rx_func() == 0
    connected_radio._transport.query.assert_called_once_with("FR;")


@pytest.mark.asyncio
async def test_get_tx_func(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="FT1")
    assert await connected_radio.get_tx_func() == 1
    connected_radio._transport.query.assert_called_once_with("FT;")


# ---------------------------------------------------------------------------
# TransceiverBankCapable (FT command / tx_source)
# ---------------------------------------------------------------------------


def test_transceiver_count_ftx1(radio):
    """FTX-1 exposes two independent transceivers (HF+50, 144+430)."""
    assert radio.transceiver_count == 2


def test_transceiver_count_non_ftx1(radio):
    """Non-FTX-1 Yaesu CAT rigs default to a single transceiver."""
    # FTX-1 is currently the only Yaesu CAT profile in-tree; simulate a
    # non-FTX-1 rig by swapping the loaded config id.
    object.__setattr__(radio._config, "id", "yaesu_other")
    assert radio.transceiver_count == 1


@pytest.mark.asyncio
async def test_set_tx_source_main(connected_radio):
    """set_tx_source(0) writes FT0; (MAIN-side transmitter)."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_tx_source(0)
    connected_radio._transport.write.assert_called_once_with("FT0;")


@pytest.mark.asyncio
async def test_set_tx_source_sub(connected_radio):
    """set_tx_source(1) writes FT1; (SUB-side transmitter)."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_tx_source(1)
    connected_radio._transport.write.assert_called_once_with("FT1;")


@pytest.mark.asyncio
async def test_set_tx_source_rejects_out_of_range(connected_radio):
    """set_tx_source rejects xcvr values other than 0 or 1."""
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError):
        await connected_radio.set_tx_source(2)
    connected_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_get_tx_source_sub(connected_radio):
    """get_tx_source parses FT1; and returns 1 (SUB-side active)."""
    connected_radio._transport.query = AsyncMock(return_value="FT1")
    assert await connected_radio.get_tx_source() == 1
    connected_radio._transport.query.assert_called_once_with("FT;")


@pytest.mark.asyncio
async def test_get_tx_source_main(connected_radio):
    """get_tx_source parses FT0; and returns 0 (MAIN-side active)."""
    connected_radio._transport.query = AsyncMock(return_value="FT0")
    assert await connected_radio.get_tx_source() == 0


# ---------------------------------------------------------------------------
# TransceiverBankCapable (set_cross_band_split)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_cross_band_split_rx0_tx1(connected_radio):
    """set_cross_band_split(rx=0, tx=1) sends FR00;, VS0;, FT1; in order."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_cross_band_split(rx_xcvr=0, tx_xcvr=1)
    assert connected_radio._transport.write.call_args_list == [
        call("FR00;"),
        call("VS0;"),
        call("FT1;"),
    ]


@pytest.mark.asyncio
async def test_set_cross_band_split_rx1_tx0(connected_radio):
    """set_cross_band_split(rx=1, tx=0) sends FR00;, VS1;, FT0; in order."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_cross_band_split(rx_xcvr=1, tx_xcvr=0)
    assert connected_radio._transport.write.call_args_list == [
        call("FR00;"),
        call("VS1;"),
        call("FT0;"),
    ]


@pytest.mark.asyncio
async def test_set_cross_band_split_rejects_same_xcvr(connected_radio):
    """set_cross_band_split raises ValueError when rx_xcvr == tx_xcvr."""
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="cross-band split requires different"):
        await connected_radio.set_cross_band_split(rx_xcvr=0, tx_xcvr=0)
    connected_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_set_cross_band_split_rejects_out_of_range_rx(connected_radio):
    """set_cross_band_split raises ValueError for rx_xcvr out of range."""
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="rx_xcvr"):
        await connected_radio.set_cross_band_split(rx_xcvr=2, tx_xcvr=0)
    connected_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_set_cross_band_split_rejects_out_of_range_tx(connected_radio):
    """set_cross_band_split raises ValueError for tx_xcvr out of range."""
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="tx_xcvr"):
        await connected_radio.set_cross_band_split(rx_xcvr=0, tx_xcvr=2)
    connected_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_get_vfo_select(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="VS0")
    assert await connected_radio.get_vfo_select() == 0
    connected_radio._transport.query.assert_called_once_with("VS;")


@pytest.mark.asyncio
async def test_vfo_a_to_b(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.vfo_a_to_b()
    connected_radio._transport.write.assert_called_once_with("AB;")


@pytest.mark.asyncio
async def test_vfo_b_to_a(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.vfo_b_to_a()
    connected_radio._transport.write.assert_called_once_with("BA;")


# ---------------------------------------------------------------------------
# D6: TX Stack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_power(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="PC2100")
    head, watts = await connected_radio.get_power()
    assert head == 2
    assert watts == 100
    connected_radio._transport.query.assert_called_once_with("PC;")


@pytest.mark.asyncio
async def test_set_power(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_power(50, head=2)
    connected_radio._transport.write.assert_called_once_with("PC2050;")


@pytest.mark.asyncio
async def test_get_mic_gain(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="MG050")
    assert await connected_radio.get_mic_gain() == 50
    connected_radio._transport.query.assert_called_once_with("MG;")


@pytest.mark.asyncio
async def test_get_processor(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="PR01")
    assert await connected_radio.get_processor() is True


# Monitor (ML) — FTX-1 does not support ML command via CAT (returns ?;)


@pytest.mark.asyncio
async def test_get_monitor_on_raises_not_implemented(connected_radio):
    with pytest.raises(NotImplementedError, match="Monitor not supported"):
        await connected_radio.get_monitor_on()


@pytest.mark.asyncio
async def test_set_monitor_on_raises_not_implemented(connected_radio):
    with pytest.raises(NotImplementedError, match="Monitor not supported"):
        await connected_radio.set_monitor_on(True)


# ---------------------------------------------------------------------------
# D7: CW
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_keyer_speed(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="KS020")
    assert await connected_radio.get_keyer_speed() == 20
    connected_radio._transport.query.assert_called_once_with("KS;")


@pytest.mark.asyncio
async def test_set_keyer_speed(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_keyer_speed(25)
    connected_radio._transport.write.assert_called_once_with("KS025;")


@pytest.mark.asyncio
async def test_get_key_pitch(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="KP30")
    assert await connected_radio.get_key_pitch() == 30
    connected_radio._transport.query.assert_called_once_with("KP;")


@pytest.mark.asyncio
async def test_send_cw(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.send_cw("0", "CQ DE W1ABC")
    connected_radio._transport.write.assert_called_once_with("KY0CQ DE W1ABC;")


@pytest.mark.asyncio
async def test_get_break_in_delay(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="SD0300")
    assert await connected_radio.get_break_in_delay() == 300
    connected_radio._transport.query.assert_called_once_with("SD;")


@pytest.mark.asyncio
async def test_set_break_in_delay(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_break_in_delay(500)
    connected_radio._transport.write.assert_called_once_with("SD0500;")


@pytest.mark.asyncio
async def test_get_break_in(connected_radio):
    from rigplane.types import BreakInMode

    connected_radio._transport.query = AsyncMock(return_value="BI1")
    result = await connected_radio.get_break_in()
    assert result == BreakInMode.SEMI
    # IntEnum stays bool-compatible at runtime.
    assert bool(result) is True


@pytest.mark.asyncio
async def test_get_break_in_off_maps_to_off(connected_radio):
    from rigplane.types import BreakInMode

    connected_radio._transport.query = AsyncMock(return_value="BI0")
    result = await connected_radio.get_break_in()
    assert result == BreakInMode.OFF
    assert bool(result) is False


@pytest.mark.asyncio
async def test_set_break_in_accepts_break_in_mode(connected_radio):
    from rigplane.types import BreakInMode

    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_break_in(BreakInMode.SEMI)
    connected_radio._transport.write.assert_called_once_with("BI1;")


@pytest.mark.asyncio
async def test_set_break_in_full_maps_to_on(connected_radio):
    from rigplane.types import BreakInMode

    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_break_in(BreakInMode.FULL)
    connected_radio._transport.write.assert_called_once_with("BI1;")


@pytest.mark.asyncio
async def test_set_break_in_off_maps_to_off(connected_radio):
    from rigplane.types import BreakInMode

    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_break_in(BreakInMode.OFF)
    connected_radio._transport.write.assert_called_once_with("BI0;")


@pytest.mark.asyncio
async def test_set_break_in_accepts_bool_for_backward_compat(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_break_in(True)
    connected_radio._transport.write.assert_called_once_with("BI1;")
    connected_radio._transport.write.reset_mock()
    await connected_radio.set_break_in(False)
    connected_radio._transport.write.assert_called_once_with("BI0;")


@pytest.mark.asyncio
async def test_get_cw_spot(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CS0")
    assert await connected_radio.get_cw_spot() is False


# ---------------------------------------------------------------------------
# D8: Clarifier (RIT/XIT)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_clarifier_both_off(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CF00000000")
    rx, tx = await connected_radio.get_clarifier()
    assert rx is False
    assert tx is False
    connected_radio._transport.query.assert_called_once_with("CF000;")


@pytest.mark.asyncio
async def test_get_clarifier_rx_on(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CF00010000")
    rx, tx = await connected_radio.get_clarifier()
    assert rx is True
    assert tx is False


@pytest.mark.asyncio
async def test_set_clarifier(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_clarifier(True, False)
    connected_radio._transport.write.assert_called_once_with("CF00010000;")


@pytest.mark.asyncio
async def test_get_clarifier_freq_positive(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CF001+0600")
    assert await connected_radio.get_clarifier_freq() == 600
    connected_radio._transport.query.assert_called_once_with("CF001;")


@pytest.mark.asyncio
async def test_get_clarifier_freq_negative(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CF001-0400")
    assert await connected_radio.get_clarifier_freq() == -400


@pytest.mark.asyncio
async def test_set_clarifier_freq(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_clarifier_freq(-250)
    connected_radio._transport.write.assert_called_once_with("CF001-0250;")


@pytest.mark.asyncio
async def test_reset_clarifier(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.reset_clarifier()
    connected_radio._transport.write.assert_called_once_with("RC;")


# ---------------------------------------------------------------------------
# Canonical RIT/XIT surface (RitXitCapable) — delegates to *_clarifier* helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rit_frequency_delegates(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CF001-0250")
    assert await connected_radio.get_rit_frequency() == -250


@pytest.mark.asyncio
async def test_set_rit_frequency_delegates(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_rit_frequency(600)
    connected_radio._transport.write.assert_called_once_with("CF001+0600;")


@pytest.mark.asyncio
async def test_get_rit_status_returns_rx_bit(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CF00010000")
    assert await connected_radio.get_rit_status() is True


@pytest.mark.asyncio
async def test_get_rit_tx_status_returns_tx_bit(connected_radio):
    # CF000 layout: CF000 + {rx:1d} + {tx:1d} + {pad:03d}
    connected_radio._transport.query = AsyncMock(return_value="CF00001000")
    assert await connected_radio.get_rit_tx_status() is True


@pytest.mark.asyncio
async def test_set_rit_status_preserves_xit(connected_radio):
    """P1-02 fix: enabling RIT must not clobber an active XIT bit."""
    # Rig currently reports RIT=off, XIT=on (CF000 = 0 1 000).
    connected_radio._transport.query = AsyncMock(return_value="CF00001000")
    connected_radio._transport.write = AsyncMock()

    await connected_radio.set_rit_status(True)

    # Wire frame must carry RX=1, TX=1 — XIT bit preserved by RMW.
    connected_radio._transport.write.assert_called_once_with("CF00011000;")


@pytest.mark.asyncio
async def test_set_rit_tx_status_preserves_rit(connected_radio):
    """P1-02 fix: enabling XIT must not clobber an active RIT bit."""
    # Rig currently reports RIT=on, XIT=off (CF000 = 1 0 000).
    connected_radio._transport.query = AsyncMock(return_value="CF00010000")
    connected_radio._transport.write = AsyncMock()

    await connected_radio.set_rit_tx_status(True)

    # Wire frame must carry RX=1, TX=1 — RIT bit preserved by RMW.
    connected_radio._transport.write.assert_called_once_with("CF00011000;")


@pytest.mark.asyncio
async def test_set_rit_status_off_preserves_xit(connected_radio):
    """RMW symmetry: turning RIT off must not flip XIT off too."""
    # Rig currently reports RIT=on, XIT=on (CF000 = 1 1 000).
    connected_radio._transport.query = AsyncMock(return_value="CF00011000")
    connected_radio._transport.write = AsyncMock()

    await connected_radio.set_rit_status(False)

    # XIT must remain set: RX=0, TX=1.
    connected_radio._transport.write.assert_called_once_with("CF00001000;")


# ---------------------------------------------------------------------------
# APF (Audio Peak Filter, CO02/CO03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_apf_off(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CO020000")
    assert await connected_radio.get_apf() is False
    connected_radio._transport.query.assert_called_once_with("CO02;")


@pytest.mark.asyncio
async def test_get_apf_on(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CO020001")
    assert await connected_radio.get_apf() is True


@pytest.mark.asyncio
async def test_set_apf_on(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_apf(True)
    connected_radio._transport.write.assert_called_once_with("CO020001;")


@pytest.mark.asyncio
async def test_set_apf_off(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_apf(False)
    connected_radio._transport.write.assert_called_once_with("CO020000;")


@pytest.mark.asyncio
async def test_get_apf_freq(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="CO030128")
    assert await connected_radio.get_apf_freq() == 128
    connected_radio._transport.query.assert_called_once_with("CO03;")


@pytest.mark.asyncio
async def test_set_apf_freq(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_apf_freq(200)
    connected_radio._transport.write.assert_called_once_with("CO030200;")


# ---------------------------------------------------------------------------
# Canonical APF adapter (DspControlCapable.set_audio_peak_filter)
# Mode 0/1/2 → Yaesu bool APF (with mode-2 raising clearly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_audio_peak_filter_mode_0_disables_apf(connected_radio):
    """Mode 0 (off) → CO020000;"""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_audio_peak_filter(0)
    connected_radio._transport.write.assert_called_once_with("CO020000;")


@pytest.mark.asyncio
async def test_set_audio_peak_filter_mode_1_enables_apf_only(
    connected_radio,
):
    """Mode 1 (soft) → APF on (CO020001;) only — no implicit freq reset.

    Regression for #1141: an unconditional `set_apf_freq(0)` would clobber
    any user-tuned APF centre frequency every time APF was toggled on.
    """
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_audio_peak_filter(1)
    connected_radio._transport.write.assert_called_once_with("CO020001;")


@pytest.mark.asyncio
async def test_set_audio_peak_filter_mode_1_preserves_freq_across_toggle(
    connected_radio,
):
    """APF centre frequency must survive an off/on toggle (#1141).

    Sequence: enable APF (mode 1) → tune freq → disable (mode 0) →
    re-enable (mode 1). Only the bool toggles and the explicit freq set
    must hit the wire; nothing in `set_audio_peak_filter` may overwrite
    the user-set frequency.
    """
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_audio_peak_filter(1)
    await connected_radio.set_apf_freq(200)
    await connected_radio.set_audio_peak_filter(0)
    await connected_radio.set_audio_peak_filter(1)
    sent = [c.args[0] for c in connected_radio._transport.write.await_args_list]
    assert sent == ["CO020001;", "CO030200;", "CO020000;", "CO020001;"]
    # Critical: only one CO03 frame — the explicit user one.
    assert sum(1 for s in sent if s.startswith("CO03")) == 1


@pytest.mark.asyncio
async def test_set_audio_peak_filter_mode_2_raises(connected_radio):
    """Mode 2 (sharp) → NotImplementedError; Yaesu has no sharp mode."""
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(NotImplementedError, match="sharp"):
        await connected_radio.set_audio_peak_filter(2)
    connected_radio._transport.write.assert_not_called()


# ---------------------------------------------------------------------------
# D9: Tone/TSQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sql_type(connected_radio):
    # MOR-473: the live FTX-1 answers ``CT0;`` with a SINGLE-digit code
    # ("CT00;"), so the read parse is ``CT0{type};`` (one digit, not two).
    connected_radio._transport.query = AsyncMock(return_value="CT00")
    assert await connected_radio.get_sql_type() == 0
    connected_radio._transport.query.assert_called_once_with("CT0;")


@pytest.mark.asyncio
async def test_get_sql_type_tone(connected_radio):
    # MOR-473: single-digit "TONE" code (1) parses to int 1.
    connected_radio._transport.query = AsyncMock(return_value="CT01")
    assert await connected_radio.get_sql_type() == 1
    connected_radio._transport.query.assert_called_once_with("CT0;")


@pytest.mark.asyncio
async def test_set_sql_type(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_sql_type(3)
    connected_radio._transport.write.assert_called_once_with("CT003;")


# -- CTCSS tone frequency (CN command, MOR-458) -----------------------------


@pytest.mark.asyncio
async def test_read_ctcss_tone_index_main(connected_radio):
    """MAIN CTCSS tone index read sends ``CN00;`` and parses ``CN00nnn;``.

    CN P1=0 (MAIN) P2=0 (CTCSS); the answer's P3 (000-049) is the tone-chart
    index. FTX-1_CAT_OM_ENG_2507.
    """
    connected_radio._transport.query = AsyncMock(return_value="CN00008")
    assert await connected_radio.read_ctcss_tone_index() == 8
    connected_radio._transport.query.assert_called_once_with("CN00;")


@pytest.mark.asyncio
async def test_read_ctcss_tone_index_is_a_pure_read(connected_radio):
    """``read_ctcss_tone_index`` must not mutate legacy ``radio_state``.

    Pre-seed an impossible combination and confirm the read leaves the
    state object identity and tone_freq/tsql_freq untouched (MOR-434 pattern).
    """
    connected_radio.radio_state.main.tone_freq = 12345
    connected_radio.radio_state.main.tsql_freq = 54321
    state_before = connected_radio.radio_state

    connected_radio._transport.query = AsyncMock(return_value="CN00049")
    assert await connected_radio.read_ctcss_tone_index() == 49
    assert connected_radio.radio_state is state_before
    assert connected_radio.radio_state.main.tone_freq == 12345
    assert connected_radio.radio_state.main.tsql_freq == 54321


@pytest.mark.asyncio
async def test_get_ctcss_tone_returns_centihz(connected_radio):
    """``get_ctcss_tone`` delegates to the index read and maps to centiHz.

    Index 8 -> 88.5 Hz -> 8850 centiHz (Icom MOR-451 convention).
    """
    connected_radio._transport.query = AsyncMock(return_value="CN00008")
    assert await connected_radio.get_ctcss_tone() == 8850


@pytest.mark.parametrize(
    ("index", "expected_centihz"),
    [
        (0, 6700),  # 67.0 Hz
        (8, 8850),  # 88.5 Hz (default CTCSS tone)
        (12, 10000),  # 100.0 Hz
        (15, 11090),  # 110.9 Hz
        (25, 15670),  # 156.7 Hz
        (49, 25410),  # 254.1 Hz (highest standard EIA tone)
    ],
)
def test_ctcss_index_to_centihz_matches_chart(index, expected_centihz):
    """Spot-check the index -> Hz -> centiHz mapping against the tone chart.

    The 50-tone EIA CTCSS chart is verbatim from FTX-1_CAT_OM_ENG_2507; the
    centiHz emission matches the Icom convention (round(Hz * 100)).
    """
    from rigplane.backends.yaesu_cat.radio import _ctcss_index_to_centihz

    assert _ctcss_index_to_centihz(index) == expected_centihz


def test_ctcss_table_has_50_standard_tones():
    """The FTX-1 CTCSS chart is the standard 50-tone EIA set (indices 0-49)."""
    from rigplane.backends.yaesu_cat.radio import _CTCSS_TONE_CENTIHZ

    assert len(_CTCSS_TONE_CENTIHZ) == 50
    assert _CTCSS_TONE_CENTIHZ[0] == 6700
    assert _CTCSS_TONE_CENTIHZ[49] == 25410


# ---------------------------------------------------------------------------
# D10: System
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_id(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="ID0840")
    model_id = await connected_radio.get_id()
    assert model_id == "0840"
    connected_radio._transport.query.assert_called_once_with("ID;")


@pytest.mark.asyncio
async def test_get_auto_info(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="AI0")
    assert await connected_radio.get_auto_info() is False


@pytest.mark.asyncio
async def test_set_auto_info(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_auto_info(True)
    connected_radio._transport.write.assert_called_once_with("AI1;")


@pytest.mark.asyncio
async def test_get_vox(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="VX1")
    assert await connected_radio.get_vox() is True


@pytest.mark.asyncio
async def test_set_vox(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_vox(False)
    connected_radio._transport.write.assert_called_once_with("VX0;")


@pytest.mark.asyncio
async def test_get_lock(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="LK0")
    assert await connected_radio.get_lock() is False


@pytest.mark.asyncio
async def test_set_lock(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_lock(True)
    connected_radio._transport.write.assert_called_once_with("LK1;")


@pytest.mark.asyncio
async def test_get_band_not_supported(connected_radio):
    """FTX-1 does not support BS read (write-only)."""
    assert not hasattr(connected_radio, "get_band")


@pytest.mark.asyncio
async def test_set_band(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_band(3)
    connected_radio._transport.write.assert_called_once_with("BS003;")


@pytest.mark.asyncio
async def test_band_up(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.band_up()
    connected_radio._transport.write.assert_called_once_with("BU0;")


@pytest.mark.asyncio
async def test_band_down(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.band_down()
    connected_radio._transport.write.assert_called_once_with("BD0;")


# ---------------------------------------------------------------------------
# AGC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agc(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="GT03")
    assert await connected_radio.get_agc() == 3
    connected_radio._transport.query.assert_called_once_with("GT0;")


@pytest.mark.asyncio
async def test_set_agc(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_agc(2)
    connected_radio._transport.write.assert_called_once_with("GT02;")


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
@pytest.mark.asyncio
async def test_set_agc_manual_modes_passthrough(connected_radio, mode):
    """MOR-498: manual AGC modes 0-3 are sent verbatim as ``GT0{mode};``."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_agc(mode)
    connected_radio._transport.write.assert_called_once_with(f"GT0{mode};")


@pytest.mark.parametrize("mode", [4, 5, 6])
@pytest.mark.asyncio
async def test_set_agc_auto_modes_map_to_gt04(connected_radio, mode):
    """MOR-498: live FTX-1 only accepts ``GT04;`` (AUTO) on SET; ``GT05;``/
    ``GT06;`` are rejected and stick at the prior value. Any AUTO request
    (read-side 4/5/6) must therefore be written as ``GT04;``."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_agc(mode)
    connected_radio._transport.write.assert_called_once_with("GT04;")


# ---------------------------------------------------------------------------
# Profile property (issue #392)
# ---------------------------------------------------------------------------


def test_profile_property_returns_radio_profile(radio):
    """YaesuCatRadio.profile returns a RadioProfile instance."""
    from rigplane.profiles import RadioProfile

    p = radio.profile
    assert isinstance(p, RadioProfile)
    assert p.model == "FTX-1"


def test_profile_property_nb_nr_controls(radio):
    """YaesuCatRadio.profile exposes NB/NR as level_is_toggle (FTX-1)."""
    p = radio.profile
    assert p.controls["nb"]["style"] == "level_is_toggle"
    assert p.controls["nr"]["style"] == "level_is_toggle"


# ---------------------------------------------------------------------------
# set_nb / set_nr — level_is_toggle (FTX-1 has no set_nb/set_nr commands)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_nb_on_calls_set_nb_level_with_default_when_current_is_zero(
    connected_radio,
):
    """set_nb(True) uses midpoint default (5) when nb_level is 0."""
    connected_radio._transport.write = AsyncMock()
    assert connected_radio._state.main.nb_level == 0
    await connected_radio.set_nb(True)
    connected_radio._transport.write.assert_called_once_with("NL0005;")


@pytest.mark.asyncio
async def test_set_nb_on_keeps_existing_level(connected_radio):
    """set_nb(True) keeps the current level when nb_level > 0."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._state.main.nb_level = 3
    await connected_radio.set_nb(True)
    connected_radio._transport.write.assert_called_once_with("NL0003;")


@pytest.mark.asyncio
async def test_set_nb_off_sends_level_zero(connected_radio):
    """set_nb(False) sends level 0 (= OFF for FTX-1)."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._state.main.nb_level = 5
    await connected_radio.set_nb(False)
    connected_radio._transport.write.assert_called_once_with("NL0000;")


@pytest.mark.asyncio
async def test_set_nr_on_calls_set_nr_level_with_default_when_current_is_zero(
    connected_radio,
):
    """set_nr(True) uses midpoint default (7) when nr_level is 0."""
    connected_radio._transport.write = AsyncMock()
    assert connected_radio._state.main.nr_level == 0
    await connected_radio.set_nr(True)
    connected_radio._transport.write.assert_called_once_with("RL007;")


@pytest.mark.asyncio
async def test_set_nr_on_keeps_existing_level(connected_radio):
    """set_nr(True) keeps the current level when nr_level > 0."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._state.main.nr_level = 4
    await connected_radio.set_nr(True)
    connected_radio._transport.write.assert_called_once_with("RL004;")


@pytest.mark.asyncio
async def test_set_nr_off_sends_level_zero(connected_radio):
    """set_nr(False) sends level 0 (= OFF for FTX-1)."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._state.main.nr_level = 7
    await connected_radio.set_nr(False)
    connected_radio._transport.write.assert_called_once_with("RL000;")


# ---------------------------------------------------------------------------
# AdvancedControlCapable aliases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cw_pitch_returns_hz_from_idx(connected_radio):
    """get_cw_pitch maps idx (0-75) → Hz via 300 + idx * 10. (#1162)"""
    connected_radio._transport.query = AsyncMock(return_value="KP25")
    # idx 25 → 300 + 25*10 = 550 Hz
    assert await connected_radio.get_cw_pitch() == 550
    connected_radio._transport.query.assert_called_once_with("KP;")


@pytest.mark.asyncio
async def test_set_cw_pitch_accepts_hz(connected_radio):
    """set_cw_pitch maps Hz → idx via (Hz - 300) // 10. (#1162)"""
    connected_radio._transport.write = AsyncMock()
    # 700 Hz → idx 40
    await connected_radio.set_cw_pitch(700)
    connected_radio._transport.write.assert_called_once_with("KP40;")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hz, idx",
    [(300, 0), (700, 40), (1050, 75)],
)
async def test_cw_pitch_round_trip_hz_idx(connected_radio, hz, idx):
    """Boundary + middle round-trips: get returns Hz, set sends idx. (#1162)"""
    connected_radio._transport.query = AsyncMock(return_value=f"KP{idx:02d}")
    assert await connected_radio.get_cw_pitch() == hz

    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_cw_pitch(hz)
    connected_radio._transport.write.assert_called_once_with(f"KP{idx:02d};")


@pytest.mark.asyncio
async def test_set_cw_pitch_rejects_out_of_range(connected_radio):
    """Hz outside 300-1050 raises ValueError. (#1162)"""
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="300-1050"):
        await connected_radio.set_cw_pitch(299)
    with pytest.raises(ValueError, match="300-1050"):
        await connected_radio.set_cw_pitch(1051)
    connected_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_get_key_pitch_still_returns_idx(connected_radio):
    """Yaesu-named get_key_pitch keeps idx contract (no break). (#1162)"""
    connected_radio._transport.query = AsyncMock(return_value="KP40")
    assert await connected_radio.get_key_pitch() == 40


@pytest.mark.asyncio
async def test_set_key_pitch_still_takes_idx(connected_radio):
    """Yaesu-named set_key_pitch keeps idx contract (no break). (#1162)"""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_key_pitch(40)
    connected_radio._transport.write.assert_called_once_with("KP40;")


@pytest.mark.asyncio
async def test_get_dial_lock_delegates_to_get_lock(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="LK1")
    assert await connected_radio.get_dial_lock() is True
    connected_radio._transport.query.assert_called_once_with("LK;")


@pytest.mark.asyncio
async def test_set_dial_lock_delegates_to_set_lock(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_dial_lock(True)
    connected_radio._transport.write.assert_called_once_with("LK1;")


@pytest.mark.asyncio
async def test_get_compressor_delegates_to_get_processor(connected_radio):
    """Yaesu get_compressor alias mirrors set_compressor → set_processor (issue #1097)."""
    connected_radio._transport.query = AsyncMock(return_value="PR01")
    assert await connected_radio.get_compressor() is True
    connected_radio._transport.query.assert_called_once_with("PR0;")


@pytest.mark.asyncio
async def test_compressor_round_trip(connected_radio):
    """Round-trip get→set→get works through the Icom-spelled protocol surface."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._transport.query = AsyncMock(side_effect=["PR00", "PR01"])

    before = await connected_radio.get_compressor()
    await connected_radio.set_compressor(True)
    after = await connected_radio.get_compressor()

    assert before is False
    assert after is True
    connected_radio._transport.write.assert_called_once_with("PR01;")


@pytest.mark.asyncio
async def test_set_compressor_delegates_to_set_processor(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_compressor(True)
    connected_radio._transport.write.assert_called_once_with("PR01;")


@pytest.mark.asyncio
async def test_set_compressor_off_delegates_to_set_processor(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_compressor(False)
    connected_radio._transport.write.assert_called_once_with("PR00;")


@pytest.mark.asyncio
async def test_get_tuner_status_delegates_to_get_tuner(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="AC001")
    result = await connected_radio.get_tuner_status()
    assert isinstance(result, int)


@pytest.mark.asyncio
async def test_set_tuner_status_delegates_to_set_tuner(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_tuner_status(1)
    connected_radio._transport.write.assert_called_once()


@pytest.mark.asyncio
async def test_send_cw_text_sends_ky_command(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.send_cw_text("CQ CQ DE W1AW")
    connected_radio._transport.write.assert_called_once_with("KY CQ CQ DE W1AW;")


@pytest.mark.asyncio
async def test_send_cw_text_splits_long_text(connected_radio):
    connected_radio._transport.write = AsyncMock()
    text = "A" * 48  # two 24-char chunks
    await connected_radio.send_cw_text(text)
    assert connected_radio._transport.write.call_count == 2


@pytest.mark.asyncio
async def test_send_cw_text_empty_sends_ky_clear(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.send_cw_text("")
    connected_radio._transport.write.assert_called_once_with("KY ;")


@pytest.mark.asyncio
async def test_stop_cw_text_sends_ky_clear(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.stop_cw_text()
    connected_radio._transport.write.assert_called_once_with("KY ;")


# ---------------------------------------------------------------------------
# RM Meters (COMP, ALC, Power, SWR, ID, VDD)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_meter_comp_zero(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM3000000")
    val = await connected_radio.get_comp_meter()
    assert val == 0
    connected_radio._transport.query.assert_called_once_with("RM3;")


@pytest.mark.asyncio
async def test_read_meter_comp_fifty(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM3050000")
    val = await connected_radio.get_comp_meter()
    assert val == 50


@pytest.mark.asyncio
async def test_read_meter_id(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM7003000")
    val = await connected_radio.get_id_meter()
    assert val == 3
    connected_radio._transport.query.assert_called_once_with("RM7;")


@pytest.mark.asyncio
async def test_read_meter_vd(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM8005000")
    val = await connected_radio.get_vd_meter()
    assert val == 5
    connected_radio._transport.query.assert_called_once_with("RM8;")


@pytest.mark.asyncio
async def test_read_meter_alc(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM4080000")
    val = await connected_radio.get_alc_meter()
    assert val == 80
    connected_radio._transport.query.assert_called_once_with("RM4;")


@pytest.mark.asyncio
async def test_read_meter_power(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM5200000")
    val = await connected_radio.get_power_meter()
    assert val == 200
    connected_radio._transport.query.assert_called_once_with("RM5;")


@pytest.mark.asyncio
async def test_read_meter_malformed_response_raises(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM00")
    with pytest.raises(ValueError, match="Malformed RM meter response"):
        await connected_radio.get_comp_meter()


@pytest.mark.asyncio
async def test_get_swr_zero_returns_one(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RM6000000")
    swr = await connected_radio.get_swr()
    assert swr == 1.0
    connected_radio._transport.query.assert_called_once_with("RM6;")


@pytest.mark.asyncio
async def test_get_swr_mid_value(connected_radio):
    # Post-#440: interpolates the TOML calibration table.
    # Calibration points: (96 → 2.0), (160 → 3.0); raw=120 interpolates
    # linearly between them.
    connected_radio._transport.query = AsyncMock(return_value="RM6120000")
    swr = await connected_radio.get_swr()
    t = (120 - 96) / (160 - 96)
    expected = 2.0 + t * (3.0 - 2.0)
    assert abs(swr - expected) < 0.01


@pytest.mark.asyncio
async def test_get_swr_max(connected_radio):
    # Post-#440: raw=255 pins to the last calibration point (5.0+),
    # replacing the legacy linear endpoint of 9.9.
    connected_radio._transport.query = AsyncMock(return_value="RM6255000")
    swr = await connected_radio.get_swr()
    assert abs(swr - 5.0) < 0.01


@pytest.mark.asyncio
async def test_get_swr_calibration_endpoints(connected_radio):
    """SWR calibration points from TOML are honored exactly (closes #440)."""
    for raw_val, expected in [(48, 1.5), (96, 2.0), (160, 3.0)]:
        connected_radio._transport.query = AsyncMock(
            return_value=f"RM6{raw_val:03d}000"
        )
        swr = await connected_radio.get_swr()
        assert abs(swr - expected) < 0.01, (
            f"raw={raw_val} expected SWR {expected}, got {swr:.3f}"
        )


@pytest.mark.asyncio
async def test_get_rf_power_delegates_to_get_power(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="PC2005")
    watts = await connected_radio.get_rf_power()
    assert watts == 5


# ---------------------------------------------------------------------------
# Processor level (PL command) — bug #549
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_processor_level_parses_3_digit(connected_radio):
    """PL command returns 3-digit level; get_processor_level parses it."""
    connected_radio._transport.query = AsyncMock(return_value="PL045")
    level = await connected_radio.get_processor_level()
    assert level == 45
    connected_radio._transport.query.assert_called_once_with("PL;")


@pytest.mark.asyncio
async def test_set_processor_level_sends_3_digit(connected_radio):
    """set_processor_level(75) sends exactly 'PL075;' — no drive parameter."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_processor_level(75)
    connected_radio._transport.write.assert_called_once_with("PL075;")


@pytest.mark.asyncio
async def test_get_processor_level_no_last_drive_gain_attribute(connected_radio):
    """After get_processor_level, no _last_drive_gain attribute should exist."""
    connected_radio._transport.query = AsyncMock(return_value="PL050")
    await connected_radio.get_processor_level()
    assert not hasattr(connected_radio, "_last_drive_gain")


@pytest.mark.asyncio
async def test_set_compressor_level_delegates_to_set_processor_level(connected_radio):
    """set_compressor_level is a thin alias for set_processor_level (issue #1098)."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_compressor_level(75)
    connected_radio._transport.write.assert_called_once_with("PL075;")


@pytest.mark.asyncio
async def test_get_compressor_level_delegates_to_get_processor_level(connected_radio):
    """get_compressor_level is a thin alias for get_processor_level (issue #1098)."""
    connected_radio._transport.query = AsyncMock(return_value="PL045")
    level = await connected_radio.get_compressor_level()
    assert level == 45
    connected_radio._transport.query.assert_called_once_with("PL;")


@pytest.mark.asyncio
async def test_compressor_level_round_trip(connected_radio):
    """Round-trip set→get reports the value just written (was 0 before #1098)."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._transport.query = AsyncMock(return_value="PL060")
    await connected_radio.set_compressor_level(60)
    level = await connected_radio.get_compressor_level()
    assert level == 60
    connected_radio._transport.write.assert_called_once_with("PL060;")
    connected_radio._transport.query.assert_called_once_with("PL;")


# ---------------------------------------------------------------------------
# Bug #550: capabilities must not advertise unimplemented features
# ---------------------------------------------------------------------------


class TestCapabilitiesNoFalseAdvertising:
    """Verify that features raising NotImplementedError are NOT in capabilities."""

    def test_repeater_tone_not_in_capabilities(self, radio):
        assert "repeater_tone" not in radio.capabilities

    def test_tsql_not_in_capabilities(self, radio):
        assert "tsql" not in radio.capabilities

    def test_data_mode_not_in_capabilities(self, radio):
        assert "data_mode" not in radio.capabilities

    def test_scan_not_in_capabilities(self, radio):
        assert "scan" not in radio.capabilities

    def test_real_capabilities_still_present(self, radio):
        """Regression: removing false caps must not break real ones."""
        for cap in ("audio", "dual_rx", "compressor", "meters", "tx", "cw"):
            assert cap in radio.capabilities, f"{cap!r} should be in capabilities"


# ---------------------------------------------------------------------------
# SUB receiver level routing (#562)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_af_level_main_sends_ag0(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="AG0128")
    level = await connected_radio.get_af_level(0)
    assert level == 128
    connected_radio._transport.query.assert_called_once_with("AG0;")


@pytest.mark.asyncio
async def test_get_af_level_sub_sends_ag1(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="AG1200")
    level = await connected_radio.get_af_level(1)
    assert level == 200
    connected_radio._transport.query.assert_called_once_with("AG1;")


@pytest.mark.asyncio
async def test_set_af_level_sub_sends_ag1(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_af_level(150, receiver=1)
    connected_radio._transport.write.assert_called_once_with("AG1150;")


@pytest.mark.asyncio
async def test_get_rf_gain_sub_sends_rg1(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="RG1180")
    level = await connected_radio.get_rf_gain(1)
    assert level == 180
    connected_radio._transport.query.assert_called_once_with("RG1;")


@pytest.mark.asyncio
async def test_get_squelch_sub_sends_sq1(connected_radio):
    connected_radio._transport.query = AsyncMock(return_value="SQ1050")
    level = await connected_radio.get_squelch(1)
    assert level == 50
    connected_radio._transport.query.assert_called_once_with("SQ1;")


@pytest.mark.asyncio
async def test_set_rf_gain_sub_sends_rg1(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_rf_gain(180, receiver=1)
    connected_radio._transport.write.assert_called_once_with("RG1180;")


@pytest.mark.asyncio
async def test_set_squelch_sub_sends_sq1(connected_radio):
    connected_radio._transport.write = AsyncMock()
    await connected_radio.set_squelch(50, receiver=1)
    connected_radio._transport.write.assert_called_once_with("SQ1050;")


# ---------------------------------------------------------------------------
# IF Bulk Query
# ---------------------------------------------------------------------------


class TestIFBulkQuery:
    """Tests for get_if_status() — Yaesu IF; composite response."""

    @pytest.mark.asyncio
    async def test_get_if_status_parses_all_fields(self, connected_radio):
        """IF response is parsed into freq, mode, RIT, PTT, split, VFO."""
        # IF + freq(9) + sign(1) + offset(4) + rit(1) + xit(1)
        # + bank(1) + chan(2) + tx(1) + mode(1) + vfo(1) + scan(1) + split(1)
        #      014074000    +    0120    1    0    0    01    0    2    0    0    1
        response = "IF014074000+01201000102001"
        connected_radio._transport.query = AsyncMock(return_value=response)

        result = await connected_radio.get_if_status()

        assert result["freq"] == 14_074_000
        assert result["mode"] == "USB"  # code "2"
        assert result["rit_offset"] == 120
        assert result["rit_on"] is True
        assert result["xit_on"] is False
        assert result["tx"] is False
        assert result["vfo"] == 0
        assert result["split"] is True

    @pytest.mark.asyncio
    async def test_get_if_status_populates_state(self, connected_radio):
        """get_if_status() must update radio_state atomically."""
        # body: freq=007074000, sign=-, offset=0050, rit=1, xit=0,
        #       bank=0, chan=01, tx=0, mode=1(LSB), vfo=0, scan=0, split=0
        response = "IF007074000-00501000101000"
        connected_radio._transport.query = AsyncMock(return_value=response)

        await connected_radio.get_if_status()

        st = connected_radio.radio_state
        assert st.main.freq == 7_074_000
        assert st.main.mode == "LSB"  # code "1"
        assert st.rit_freq == -50
        assert st.rit_on is True
        assert st.ptt is False
        assert st.split is False

    @pytest.mark.asyncio
    async def test_get_if_status_tx_active(self, connected_radio):
        """PTT=1 in IF response sets state.ptt = True."""
        response = "IF014074000+000000001120000"
        connected_radio._transport.query = AsyncMock(return_value=response)

        result = await connected_radio.get_if_status()

        assert result["tx"] is True
        assert connected_radio.radio_state.ptt is True

    @pytest.mark.asyncio
    async def test_get_if_status_invalid_response_raises(self, connected_radio):
        """Short or malformed IF response raises CommandError."""
        connected_radio._transport.query = AsyncMock(return_value="IF00")

        with pytest.raises(CommandError):
            await connected_radio.get_if_status()

    @pytest.mark.asyncio
    async def test_connect_calls_if_status(self, radio):
        """connect() should attempt IF bulk query to seed state."""
        radio._transport.connect = AsyncMock()
        radio._transport._connected = True
        radio._transport.query = AsyncMock(return_value="IF014074000+000000001020000")

        await radio.connect()

        radio._transport.query.assert_called_once_with("IF;")
        assert radio.radio_state.main.freq == 14_074_000

    @pytest.mark.asyncio
    async def test_connect_succeeds_if_if_query_fails(self, radio):
        """connect() must not fail if IF; query times out or errors."""
        radio._transport.connect = AsyncMock()
        radio._transport._connected = True
        radio._transport.query = AsyncMock(side_effect=Exception("timeout"))

        await radio.connect()  # should not raise

        assert radio.connected


# ---------------------------------------------------------------------------
# ReceiverBankCapable / VfoSlotCapable (#1171)
# ---------------------------------------------------------------------------


from rigplane.backends.yaesu_cat.parser import CatCommandParser  # noqa: E402
from rigplane.command_spec import CatCommandSpec  # noqa: E402
from rigplane.radio_protocol import (  # noqa: E402
    ReceiverBankCapable,
    VfoSlotCapable,
)


@pytest.fixture()
def single_rx_radio(config):
    """YaesuCatRadio mutated to model a single-RX Yaesu CAT rig.

    Lab599 TX-500 (Kenwood CAT) and FT-710 / FT-991A (Yaesu CAT, no
    in-tree TOML yet) all expose a single receiver with VFO A/B routed
    via ``FR;`` (``FR0;`` = VFO-A, ``FR1;`` = VFO-B).  We model that by
    cloning the FTX-1 config and overriding ``receiver_count``,
    ``vfo_scheme`` and the ``set_vfo_select`` / ``get_vfo_select``
    command templates to point at ``FR;`` instead of ``VS;``.
    """
    cfg = config
    new_commands = dict(cfg.commands)
    new_commands["get_vfo_select"] = CatCommandSpec(read="FR;", parse="FR{vfo};")
    new_commands["set_vfo_select"] = CatCommandSpec(write="FR{vfo};")
    object.__setattr__(cfg, "commands", new_commands)
    object.__setattr__(cfg, "receiver_count", 1)
    object.__setattr__(cfg, "vfo_scheme", "ab")
    r = YaesuCatRadio("/dev/null", profile=cfg)
    # Rebuild parser cache so the swapped templates take effect.
    r._parsers["get_vfo_select"] = CatCommandParser("FR{vfo};")
    r._transport._connected = True
    return r


def test_receiver_count_ftx1(radio):
    """FTX-1 profile reports receiver_count == 2 (MAIN + SUB)."""
    assert radio.receiver_count == 2


def test_receiver_count_single_rx(single_rx_radio):
    """Single-RX synthetic profile reports receiver_count == 1."""
    assert single_rx_radio.receiver_count == 1


def test_protocol_satisfaction_receiver_bank(radio):
    """YaesuCatRadio satisfies ReceiverBankCapable on FTX-1."""
    assert isinstance(radio, ReceiverBankCapable)


def test_protocol_satisfaction_vfo_slot(radio):
    """YaesuCatRadio satisfies VfoSlotCapable on FTX-1."""
    assert isinstance(radio, VfoSlotCapable)


def test_protocol_satisfaction_single_rx(single_rx_radio):
    """Single-RX YaesuCatRadio satisfies both protocols."""
    assert isinstance(single_rx_radio, ReceiverBankCapable)
    assert isinstance(single_rx_radio, VfoSlotCapable)


# -- ReceiverBankCapable.select_receiver / get_active_receiver -------------


@pytest.mark.asyncio
async def test_select_receiver_main_writes_vs0(connected_radio):
    """select_receiver(0) on dual-RX FTX-1 emits VS0;."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.select_receiver(0)
    connected_radio._transport.write.assert_called_once_with("VS0;")


@pytest.mark.asyncio
async def test_select_receiver_sub_writes_vs1(connected_radio):
    """select_receiver(1) on dual-RX FTX-1 emits VS1;."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.select_receiver(1)
    connected_radio._transport.write.assert_called_once_with("VS1;")


@pytest.mark.asyncio
async def test_select_receiver_by_name_main(connected_radio):
    """select_receiver('main') normalizes to index 0 → VS0;."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.select_receiver("main")
    connected_radio._transport.write.assert_called_once_with("VS0;")


@pytest.mark.asyncio
async def test_select_receiver_by_name_sub_case_insensitive(connected_radio):
    """select_receiver('SUB') is case-insensitive → VS1;."""
    connected_radio._transport.write = AsyncMock()
    await connected_radio.select_receiver("SUB")
    connected_radio._transport.write.assert_called_once_with("VS1;")


@pytest.mark.asyncio
async def test_select_receiver_unknown_name_raises(connected_radio):
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="unknown receiver name"):
        await connected_radio.select_receiver("tertiary")
    connected_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_select_receiver_out_of_range_raises(connected_radio):
    connected_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="out of range"):
        await connected_radio.select_receiver(2)
    connected_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_select_receiver_single_rx_zero_is_noop(single_rx_radio):
    """On single-RX rigs select_receiver(0) is a no-op (no wire traffic)."""
    single_rx_radio._transport.write = AsyncMock()
    await single_rx_radio.select_receiver(0)
    single_rx_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_select_receiver_single_rx_nonzero_raises(single_rx_radio):
    single_rx_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="out of range"):
        await single_rx_radio.select_receiver(1)
    single_rx_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_get_active_receiver_dual_rx(connected_radio):
    """get_active_receiver parses VS1; → 1 on dual-RX FTX-1."""
    connected_radio._transport.query = AsyncMock(return_value="VS1")
    assert await connected_radio.get_active_receiver() == 1
    connected_radio._transport.query.assert_called_once_with("VS;")


@pytest.mark.asyncio
async def test_get_active_receiver_single_rx_returns_zero(single_rx_radio):
    """Single-RX returns 0 without wire traffic."""
    single_rx_radio._transport.query = AsyncMock()
    assert await single_rx_radio.get_active_receiver() == 0
    single_rx_radio._transport.query.assert_not_called()


@pytest.mark.asyncio
async def test_select_receiver_roundtrip_dual_rx(connected_radio):
    """select_receiver(1) → get_active_receiver returns 1 (mocked)."""
    connected_radio._transport.write = AsyncMock()
    connected_radio._transport.query = AsyncMock(return_value="VS1")
    await connected_radio.select_receiver(1)
    assert await connected_radio.get_active_receiver() == 1


# -- VfoSlotCapable on FTX-1 (ab_shared scheme — raises) -------------------


@pytest.mark.asyncio
async def test_get_vfo_slot_raises_on_ftx1(connected_radio):
    """FTX-1 has no per-receiver A/B; get_vfo_slot raises NotImplementedError."""
    with pytest.raises(NotImplementedError, match="ab_shared"):
        await connected_radio.get_vfo_slot()


@pytest.mark.asyncio
async def test_set_vfo_slot_raises_on_ftx1(connected_radio):
    with pytest.raises(NotImplementedError, match="ab_shared"):
        await connected_radio.set_vfo_slot("A")


@pytest.mark.asyncio
async def test_swap_vfo_ab_raises_on_ftx1(connected_radio):
    """FTX-1 AB;/BA; copy MAIN↔SUB, not A↔B; swap_vfo_ab raises."""
    with pytest.raises(NotImplementedError, match="no symmetric"):
        await connected_radio.swap_vfo_ab()


@pytest.mark.asyncio
async def test_equalize_vfo_ab_raises_on_ftx1(connected_radio):
    with pytest.raises(NotImplementedError, match="no per-receiver"):
        await connected_radio.equalize_vfo_ab()


# -- VfoSlotCapable on single-RX (FR; scheme — works) ----------------------


@pytest.mark.asyncio
async def test_set_vfo_slot_a_writes_fr0(single_rx_radio):
    """set_vfo_slot('A') emits FR0; on a single-RX rig."""
    single_rx_radio._transport.write = AsyncMock()
    await single_rx_radio.set_vfo_slot("A")
    single_rx_radio._transport.write.assert_called_once_with("FR0;")


@pytest.mark.asyncio
async def test_set_vfo_slot_b_writes_fr1(single_rx_radio):
    """set_vfo_slot('B') emits FR1;."""
    single_rx_radio._transport.write = AsyncMock()
    await single_rx_radio.set_vfo_slot("B")
    single_rx_radio._transport.write.assert_called_once_with("FR1;")


@pytest.mark.asyncio
async def test_set_vfo_slot_case_insensitive(single_rx_radio):
    """Lower-case slot names are accepted."""
    single_rx_radio._transport.write = AsyncMock()
    await single_rx_radio.set_vfo_slot("b")
    single_rx_radio._transport.write.assert_called_once_with("FR1;")


@pytest.mark.asyncio
async def test_set_vfo_slot_invalid_raises(single_rx_radio):
    single_rx_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="slot must be 'A' or 'B'"):
        await single_rx_radio.set_vfo_slot("C")
    single_rx_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_get_vfo_slot_a(single_rx_radio):
    """get_vfo_slot reads FR; → 'A' when radio reports FR0;."""
    single_rx_radio._transport.query = AsyncMock(return_value="FR0")
    assert await single_rx_radio.get_vfo_slot() == "A"
    single_rx_radio._transport.query.assert_called_once_with("FR;")


@pytest.mark.asyncio
async def test_get_vfo_slot_b(single_rx_radio):
    single_rx_radio._transport.query = AsyncMock(return_value="FR1")
    assert await single_rx_radio.get_vfo_slot() == "B"


@pytest.mark.asyncio
async def test_set_get_vfo_slot_roundtrip(single_rx_radio):
    """set_vfo_slot('B') then get_vfo_slot returns 'B' (mocked)."""
    single_rx_radio._transport.write = AsyncMock()
    single_rx_radio._transport.query = AsyncMock(return_value="FR1")
    await single_rx_radio.set_vfo_slot("B")
    assert await single_rx_radio.get_vfo_slot() == "B"


@pytest.mark.asyncio
async def test_swap_vfo_ab_raises_on_single_rx(single_rx_radio):
    """Yaesu CAT has no A↔B swap primitive even on single-RX rigs."""
    with pytest.raises(NotImplementedError, match="no symmetric"):
        await single_rx_radio.swap_vfo_ab()


@pytest.mark.asyncio
async def test_equalize_vfo_ab_raises_on_single_rx(single_rx_radio):
    with pytest.raises(NotImplementedError, match="no per-receiver"):
        await single_rx_radio.equalize_vfo_ab()


@pytest.mark.asyncio
async def test_set_vfo_slot_rejects_bad_receiver_index(single_rx_radio):
    single_rx_radio._transport.write = AsyncMock()
    with pytest.raises(ValueError, match="out of range"):
        await single_rx_radio.set_vfo_slot("A", receiver=1)
    single_rx_radio._transport.write.assert_not_called()


@pytest.mark.asyncio
async def test_get_vfo_slot_rejects_bad_receiver_index(single_rx_radio):
    with pytest.raises(ValueError, match="out of range"):
        await single_rx_radio.get_vfo_slot(receiver=1)


# ---------------------------------------------------------------------------
# get_manual_notch_freq (standalone freq getter — MSMA-31)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_manual_notch_freq_returns_int(connected_radio):
    """get_manual_notch_freq queries BP01 and returns the freq index as int."""
    connected_radio._transport.query = AsyncMock(return_value="BP01080")
    freq = await connected_radio.get_manual_notch_freq()
    assert freq == 80
    assert isinstance(freq, int)
    connected_radio._transport.query.assert_called_once_with("BP01;")


@pytest.mark.asyncio
async def test_get_manual_notch_freq_zero(connected_radio):
    """get_manual_notch_freq handles zero value."""
    connected_radio._transport.query = AsyncMock(return_value="BP01000")
    assert await connected_radio.get_manual_notch_freq() == 0


@pytest.mark.asyncio
async def test_get_manual_notch_freq_max(connected_radio):
    """get_manual_notch_freq handles max value (255)."""
    connected_radio._transport.query = AsyncMock(return_value="BP01255")
    assert await connected_radio.get_manual_notch_freq() == 255


@pytest.mark.asyncio
async def test_get_manual_notch_freq_independent_of_get_manual_notch(connected_radio):
    """get_manual_notch_freq issues a single BP01 query (not BP00+BP01 like get_manual_notch)."""
    connected_radio._transport.query = AsyncMock(return_value="BP01120")
    await connected_radio.get_manual_notch_freq()
    assert connected_radio._transport.query.call_count == 1


# ---------------------------------------------------------------------------
# get_meter — unified dispatcher (MSMA-31)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_meter_smeter_string(connected_radio):
    """get_meter('smeter') routes to get_s_meter on main receiver."""
    connected_radio._transport.query = AsyncMock(return_value="SM0042")
    val = await connected_radio.get_meter("smeter")
    assert val == 42
    connected_radio._transport.query.assert_called_once_with("SM0;")


@pytest.mark.asyncio
async def test_get_meter_smeter_sub_receiver(connected_radio):
    """get_meter('smeter', receiver=1) routes to get_s_meter_sub."""
    connected_radio._transport.query = AsyncMock(return_value="SM1015")
    val = await connected_radio.get_meter("smeter", receiver=1)
    assert val == 15
    connected_radio._transport.query.assert_called_once_with("SM1;")


@pytest.mark.asyncio
async def test_get_meter_comp_string(connected_radio):
    """get_meter('comp') routes to get_comp_meter (RM3)."""
    connected_radio._transport.query = AsyncMock(return_value="RM3050000")
    val = await connected_radio.get_meter("comp")
    assert val == 50
    connected_radio._transport.query.assert_called_once_with("RM3;")


@pytest.mark.asyncio
async def test_get_meter_alc_string(connected_radio):
    """get_meter('alc') routes to get_alc_meter (RM4)."""
    connected_radio._transport.query = AsyncMock(return_value="RM4080000")
    val = await connected_radio.get_meter("alc")
    assert val == 80
    connected_radio._transport.query.assert_called_once_with("RM4;")


@pytest.mark.asyncio
async def test_get_meter_power_string(connected_radio):
    """get_meter('power') routes to get_power_meter (RM5)."""
    connected_radio._transport.query = AsyncMock(return_value="RM5200000")
    val = await connected_radio.get_meter("power")
    assert val == 200
    connected_radio._transport.query.assert_called_once_with("RM5;")


@pytest.mark.asyncio
async def test_get_meter_swr_string(connected_radio):
    """get_meter('swr') routes to get_swr_meter (RM6 raw, not interpolated)."""
    connected_radio._transport.query = AsyncMock(return_value="RM6120000")
    val = await connected_radio.get_meter("swr")
    assert val == 120
    connected_radio._transport.query.assert_called_once_with("RM6;")


@pytest.mark.asyncio
async def test_get_meter_current_string(connected_radio):
    """get_meter('id') routes to get_id_meter (RM7)."""
    connected_radio._transport.query = AsyncMock(return_value="RM7003000")
    val = await connected_radio.get_meter("id")
    assert val == 3
    connected_radio._transport.query.assert_called_once_with("RM7;")


@pytest.mark.asyncio
async def test_get_meter_voltage_string(connected_radio):
    """get_meter('vd') routes to get_vd_meter (RM8)."""
    connected_radio._transport.query = AsyncMock(return_value="RM8005000")
    val = await connected_radio.get_meter("vd")
    assert val == 5
    connected_radio._transport.query.assert_called_once_with("RM8;")


@pytest.mark.asyncio
async def test_get_meter_accepts_meter_type_enum(connected_radio):
    """get_meter also accepts a MeterType enum value."""
    from rigplane.meter_cal import MeterType

    connected_radio._transport.query = AsyncMock(return_value="SM0100")
    val = await connected_radio.get_meter(MeterType.SMETER)
    assert val == 100


@pytest.mark.asyncio
async def test_get_meter_unknown_type_raises(connected_radio):
    """get_meter raises ValueError for an unrecognised meter type string."""
    with pytest.raises(ValueError, match="Unknown meter type"):
        await connected_radio.get_meter("no_such_meter")
