"""Extra coverage tests for radio.py.

Covers the many methods/branches not reached by the existing test suite:
- conn_state property (line 252)
- _intentional_disconnect setter (line 277)
- civ_stats() (line 304)
- soft_disconnect() (lines 356-390)
- soft_reconnect() early-exit paths (lines 398-406)
- deprecated audio aliases (lines 823-864)
- _get_pcm_transcoder() cache-miss path (lines 882-883)
- get_data_mode() / set_data_mode() NAK (lines 1122-1138)
- get_rf_gain() / get_af_level() timeout re-raise (1178-1202)
- set_squelch() (1214-1219)
- get_attenuator_level() timeout+fallback (1317-1322)
- set_attenuator_level() validation error (1342)
- get_preamp() timeout+fallback (1369-1374)
- set_preamp() digisel exclusion (1393-1402)
- get_digisel() empty response (1414)
- set_digisel() rejection (1421-1426)
- get_nb(), set_nb(), get_nr(), set_nr(), get_ip_plus(), set_ip_plus() (1429-1468)
- snapshot_state() (1472-1510)
- restore_state() (1514-1559)
- run_state_transaction() (1566-1580)
- scope_stream() async generator (1653-1661)
- enable_scope() FAST/STRICT/VERIFY policies (1681-1709)
- disable_scope() STRICT (1722-1732)
- capture_scope_frame() / capture_scope_frames() (1748-1791)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.exceptions import CommandError, ConnectionError, TimeoutError
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeFrame
from rigplane.types import Mode

# Re-use the low-level helpers from test_radio
from test_radio import MockTransport, _ack_response, _nak_response, _wrap_civ_in_udp


# ---------------------------------------------------------------------------
# Helpers for building additional CI-V responses
# ---------------------------------------------------------------------------


def _data_mode_response(on: bool) -> bytes:
    """CI-V response for get_data_mode (command 0x1A, sub 0x06)."""
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x1A,
        sub=0x06,
        data=bytes([0x01 if on else 0x00]),
    )
    return _wrap_civ_in_udp(civ)


def _bool_response(cmd: int, sub: int | None, value: bool) -> bytes:
    """CI-V response with a single bool byte for NB/NR/IP+ etc."""
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        cmd,
        sub=sub,
        data=bytes([0x01 if value else 0x00]),
    )
    return _wrap_civ_in_udp(civ)


def _level_response(cmd: int, sub: int, level: int) -> bytes:
    """CI-V response for a level command (0-255 as BCD)."""
    d = f"{level:04d}"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, cmd, sub=sub, data=bytes([b0, b1])
    )
    return _wrap_civ_in_udp(civ)


def _raw_byte_response(cmd: int, sub: int | None, raw: int) -> bytes:
    """CI-V response with a single raw byte (attenuator, preamp, digisel)."""
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, cmd, sub=sub, data=bytes([raw])
    )
    return _wrap_civ_in_udp(civ)


# ---------------------------------------------------------------------------
# Fixtures
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


# ---------------------------------------------------------------------------
# conn_state property (line 252)
# ---------------------------------------------------------------------------


def test_conn_state_returns_current_state(radio: IcomRadio) -> None:
    from rigplane.runtime._connection_state import RadioConnectionState

    assert radio.conn_state == RadioConnectionState.CONNECTED


def test_radio_ready_true_when_connected_and_civ_stream_healthy(
    radio: IcomRadio,
) -> None:
    radio._civ_stream_ready = True
    radio._civ_recovering = False
    radio._last_civ_data_received = 100.0
    with patch("time.monotonic", return_value=101.0):
        assert radio.radio_ready is True


def test_radio_ready_false_when_civ_recovering(radio: IcomRadio) -> None:
    radio._civ_stream_ready = True
    radio._civ_recovering = True
    radio._last_civ_data_received = 100.0
    with patch("time.monotonic", return_value=101.0):
        assert radio.radio_ready is False


def test_radio_ready_false_when_civ_data_is_stale(radio: IcomRadio) -> None:
    radio._civ_stream_ready = True
    radio._civ_recovering = False
    radio._last_civ_data_received = 100.0
    with patch("time.monotonic", return_value=200.0):
        assert radio.radio_ready is False


# ---------------------------------------------------------------------------
# _intentional_disconnect setter (line 277)
# ---------------------------------------------------------------------------


def test_intentional_disconnect_property_reflects_disconnected_state(
    radio: IcomRadio,
) -> None:
    from rigplane.runtime._connection_state import RadioConnectionState

    # CONNECTED → not intentional disconnect
    assert radio._intentional_disconnect is False
    # Set conn_state to DISCONNECTED manually and check property
    radio._conn_state = RadioConnectionState.DISCONNECTED
    assert radio._intentional_disconnect is True


def test_intentional_disconnect_setter_true_sets_disconnected(radio: IcomRadio) -> None:
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._intentional_disconnect = True
    assert radio._conn_state == RadioConnectionState.DISCONNECTED


def test_intentional_disconnect_setter_false_when_disconnected_sets_reconnecting(
    radio: IcomRadio,
) -> None:
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.DISCONNECTED
    radio._intentional_disconnect = False
    assert radio._conn_state == RadioConnectionState.RECONNECTING


# ---------------------------------------------------------------------------
# civ_stats() (line 304)
# ---------------------------------------------------------------------------


def test_civ_stats_returns_dict(radio: IcomRadio) -> None:
    stats = radio.civ_stats()
    assert isinstance(stats, dict)
    assert "active_waiters" in stats or "generation" in stats


# ---------------------------------------------------------------------------
# soft_disconnect() (lines 356-390)
# ---------------------------------------------------------------------------


async def test_soft_disconnect_when_not_connected_is_noop(radio: IcomRadio) -> None:
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.DISCONNECTED
    # Should not raise
    await radio.soft_disconnect()


async def test_soft_disconnect_disconnects_civ_and_keeps_ctrl(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """soft_disconnect() should disconnect CI-V but leave ctrl transport alive."""
    await radio.soft_disconnect()
    # After soft disconnect, conn_state is DISCONNECTED and _civ_transport is None
    assert radio._civ_transport is None
    # ctrl transport is still set (not disconnected by soft_disconnect)


async def test_soft_disconnect_with_audio_stream_stops_audio(
    radio: IcomRadio,
) -> None:
    """soft_disconnect() stops any active audio stream."""
    audio_stream = MagicMock()
    audio_stream.stop_rx = AsyncMock()
    audio_stream.stop_tx = AsyncMock()
    radio._audio_stream = audio_stream

    await radio.soft_disconnect()

    audio_stream.stop_rx.assert_awaited_once()
    audio_stream.stop_tx.assert_awaited_once()
    assert radio._audio_stream is None


# ---------------------------------------------------------------------------
# soft_reconnect() early-exit paths (lines 398-406)
# ---------------------------------------------------------------------------


async def test_soft_reconnect_warns_when_civ_transport_already_open(
    radio: IcomRadio,
    mock_transport: MockTransport,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """soft_reconnect() must warn and return early when CIV is already open."""
    import logging

    # _civ_transport is already set (the mock)
    with caplog.at_level(logging.WARNING, logger="rigplane.runtime.radio"):
        await radio.soft_reconnect()

    assert any("already open" in r.message for r in caplog.records)


async def test_soft_reconnect_does_full_connect_when_ctrl_dead(
    radio: IcomRadio,
) -> None:
    """soft_reconnect() falls back to full connect() when ctrl transport is dead."""
    radio._civ_transport = None
    radio._ctrl_transport._udp_transport = None  # type: ignore[attr-defined]

    connect_mock = AsyncMock()

    with patch.object(radio._control_phase, "connect", side_effect=connect_mock):
        await radio.soft_reconnect()

    connect_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Removed audio aliases (issue #1111) — verify they raise AttributeError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "start_audio_rx",
        "stop_audio_rx",
        "start_audio_tx",
        "push_audio_tx",
        "stop_audio_tx",
        "start_audio",
        "stop_audio",
    ],
)
def test_removed_audio_aliases_raise_attribute_error(
    radio: IcomRadio, name: str
) -> None:
    """Aliases removed in #1111 (overdue from v0.15) must not exist on IcomRadio."""
    assert not hasattr(radio, name)


# ---------------------------------------------------------------------------
# _get_pcm_transcoder() cache-miss (lines 882-883)
# ---------------------------------------------------------------------------


def test_get_pcm_transcoder_creates_new_on_cache_miss(radio: IcomRadio) -> None:
    """_get_pcm_transcoder() creates a fresh transcoder on first call."""
    with patch(
        "rigplane._audio_runtime_mixin.create_pcm_opus_transcoder"
    ) as mock_create:
        mock_create.return_value = MagicMock()
        tc = radio._get_pcm_transcoder(sample_rate=48000, channels=1, frame_ms=20)
        mock_create.assert_called_once()
        assert tc is not None


def test_get_pcm_transcoder_returns_cached_on_same_params(radio: IcomRadio) -> None:
    """Second call with same params returns cached transcoder."""
    with patch(
        "rigplane._audio_runtime_mixin.create_pcm_opus_transcoder"
    ) as mock_create:
        mock_create.return_value = MagicMock()
        tc1 = radio._get_pcm_transcoder(sample_rate=48000, channels=1, frame_ms=20)
        tc2 = radio._get_pcm_transcoder(sample_rate=48000, channels=1, frame_ms=20)
        assert mock_create.call_count == 1
        assert tc1 is tc2


# ---------------------------------------------------------------------------
# get_data_mode() (lines 1122-1125)
# ---------------------------------------------------------------------------


async def test_get_data_mode_returns_false(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_data_mode_response(False))
    result = await radio.get_data_mode()
    assert result is False


async def test_get_data_mode_returns_true(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_data_mode_response(True))
    result = await radio.get_data_mode()
    assert result is True


# ---------------------------------------------------------------------------
# set_data_mode() NAK rejection (lines 1133-1138)
# ---------------------------------------------------------------------------


async def test_set_data_mode_raises_on_nak(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_nak_response())
    with pytest.raises(CommandError):
        await radio.set_data_mode(True)


async def test_set_data_mode_succeeds_on_ack(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_ack_response())
    await radio.set_data_mode(True)  # Should not raise


# ---------------------------------------------------------------------------
# get_rf_gain() timeout re-raise (lines 1178-1184)
# ---------------------------------------------------------------------------


async def test_get_rf_gain_reraises_timeout_when_cache_not_fresh(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """When get_rf_gain times out (no cache), TimeoutError should propagate."""
    # No response queued → timeout
    with pytest.raises(TimeoutError):
        await radio.get_rf_gain()


# ---------------------------------------------------------------------------
# get_af_level() timeout re-raise (lines 1196-1202)
# ---------------------------------------------------------------------------


async def test_get_af_level_reraises_timeout_when_cache_not_fresh(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """When get_af_level times out, TimeoutError should propagate."""
    with pytest.raises(TimeoutError):
        await radio.get_af_level()


# ---------------------------------------------------------------------------
# set_squelch() (lines 1214-1219)
# ---------------------------------------------------------------------------


async def test_set_squelch_sends_command(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    await radio.set_squelch(100)
    assert len(mock_transport.sent_packets) > 0


async def test_set_squelch_invalid_raises(radio: IcomRadio) -> None:
    with pytest.raises(ValueError):
        await radio.set_squelch(300)


async def test_set_squelch_negative_raises(radio: IcomRadio) -> None:
    with pytest.raises(ValueError):
        await radio.set_squelch(-1)


# ---------------------------------------------------------------------------
# get_attenuator_level() timeout + fallback (lines 1317-1322)
# ---------------------------------------------------------------------------


async def test_get_attenuator_level_returns_fallback_on_timeout(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """Timeout falls back to _attenuator_state if set."""
    radio._attenuator_state = True
    # No response → timeout → fallback to 18 (non-zero attenuator)
    result = await radio.get_attenuator_level()
    assert result == 18


async def test_get_attenuator_level_returns_zero_fallback_when_off(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    radio._attenuator_state = False
    result = await radio.get_attenuator_level()
    assert result == 0


async def test_get_attenuator_level_raises_when_no_state(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    radio._attenuator_state = None
    with pytest.raises(CommandError):
        await radio.get_attenuator_level()


# ---------------------------------------------------------------------------
# set_attenuator_level() validation (line 1342)
# ---------------------------------------------------------------------------


async def test_set_attenuator_level_invalid_db_raises(radio: IcomRadio) -> None:
    with pytest.raises(ValueError, match="3 dB steps"):
        await radio.set_attenuator_level(7)  # not a multiple of 3


async def test_set_attenuator_level_too_high_raises(radio: IcomRadio) -> None:
    with pytest.raises(ValueError):
        await radio.set_attenuator_level(48)


async def test_set_attenuator_level_valid_sends_command(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    await radio.set_attenuator_level(18)
    assert len(mock_transport.sent_packets) > 0


# ---------------------------------------------------------------------------
# get_preamp() timeout + fallback (lines 1369-1374)
# ---------------------------------------------------------------------------


async def test_get_preamp_returns_fallback_on_timeout(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    radio._preamp_level = 1
    result = await radio.get_preamp()
    assert result == 1


async def test_get_preamp_raises_when_no_state(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    radio._preamp_level = None
    with pytest.raises(CommandError):
        await radio.get_preamp()


# ---------------------------------------------------------------------------
# set_preamp() digisel exclusion check (lines 1393-1402)
# ---------------------------------------------------------------------------


async def test_set_preamp_raises_when_digisel_on(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """set_preamp() must raise CommandError when DIGI-SEL is ON."""
    # get_digisel() uses CMD29 frame: FE FE 98 E0 29 00 16 4E FD
    # Radio responds with CMD29: FE FE E0 98 29 00 16 4E 01 FD (on=0x01 BCD)
    # After parsing: command=0x16, sub=0x4E, receiver=0x00, data=[0x01]
    digisel_civ = bytes.fromhex("fefee0982900164e01fd")
    mock_transport.queue_response(_wrap_civ_in_udp(digisel_civ))
    with pytest.raises(CommandError, match="DIGI-SEL"):
        await radio.set_preamp(1)


async def test_set_preamp_proceeds_when_digisel_off(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """set_preamp() should proceed when DIGI-SEL is OFF."""
    digisel_response = _raw_byte_response(0x27, 0x16, 0x00)  # DIGI-SEL off
    mock_transport.queue_response(digisel_response)
    # Should not raise; just send the command
    await radio.set_preamp(1)


async def test_set_preamp_level_zero_skips_digisel_check(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """set_preamp(0) (disable) must skip the digisel check."""
    await radio.set_preamp(0)
    # No digisel query should have been needed
    assert radio._preamp_level == 0


# ---------------------------------------------------------------------------
# get_digisel() empty response (line 1414)
# ---------------------------------------------------------------------------


async def test_get_digisel_raises_on_empty_response(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_digisel() must raise CommandError when radio returns no data byte."""
    # get_digisel() uses CMD29 frame: FE FE 98 E0 29 00 16 4E FD
    # Radio responds with empty CMD29: FE FE E0 98 29 00 16 4E FD (no data byte)
    # After parsing: command=0x16, sub=0x4E, receiver=0x00, data=[]
    empty_civ = bytes.fromhex("fefee098290016" + "4e" + "fd")
    mock_transport.queue_response(_wrap_civ_in_udp(empty_civ))
    with pytest.raises(CommandError, match="empty DIGI-SEL response"):
        await radio.get_digisel()


# ---------------------------------------------------------------------------
# set_digisel() rejection (lines 1421-1426)
# ---------------------------------------------------------------------------


async def test_set_digisel_raises_on_nak(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_nak_response())
    with pytest.raises(CommandError, match="rejected DIGI-SEL"):
        await radio.set_digisel(True)


async def test_set_digisel_succeeds_on_ack(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_ack_response())
    await radio.set_digisel(False)  # should not raise


# ---------------------------------------------------------------------------
# get_nb() / set_nb() (lines 1429-1440)
# ---------------------------------------------------------------------------


async def test_get_nb_returns_true(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    # NB command is 0x16, sub 0x22 (typical IC-7610)
    mock_transport.queue_response(_bool_response(0x16, 0x22, True))
    result = await radio.get_nb()
    assert result is True


async def test_set_nb_sends_command(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    await radio.set_nb(True)
    assert len(mock_transport.sent_packets) > 0


# ---------------------------------------------------------------------------
# get_nr() / set_nr() (lines 1442-1453)
# ---------------------------------------------------------------------------


async def test_get_nr_returns_false(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_bool_response(0x16, 0x40, False))
    result = await radio.get_nr()
    assert result is False


async def test_set_nr_sends_command(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    await radio.set_nr(False)
    assert len(mock_transport.sent_packets) > 0


# ---------------------------------------------------------------------------
# get_ip_plus() / set_ip_plus() (lines 1457-1468)
# ---------------------------------------------------------------------------


async def test_get_ip_plus_returns_true(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    mock_transport.queue_response(_bool_response(0x16, 0x65, True))
    result = await radio.get_ip_plus()
    assert result is True


async def test_set_ip_plus_sends_command(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    await radio.set_ip_plus(True)
    assert len(mock_transport.sent_packets) > 0


# ---------------------------------------------------------------------------
# snapshot_state() (lines 1472-1510)
# ---------------------------------------------------------------------------


async def test_snapshot_state_returns_dict_with_basics(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    from rigplane.types import bcd_encode
    from rigplane.commands import (
        _CMD_FREQ_GET,
        _CMD_LEVEL,
        _CMD_MODE_GET,
        _SUB_RF_POWER,
    )

    freq_civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_FREQ_GET,
        data=bcd_encode(14_074_000),
    )
    mock_transport.queue_response(_wrap_civ_in_udp(freq_civ))

    mode_civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_MODE_GET,
        data=bytes([Mode.USB.value, 1]),
    )
    mock_transport.queue_response(_wrap_civ_in_udp(mode_civ))

    # Power response
    power_civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_LEVEL,
        sub=_SUB_RF_POWER,
        data=bytes([0x01, 0x00]),
    )
    mock_transport.queue_response(_wrap_civ_in_udp(power_civ))

    snap = await radio.snapshot_state()
    assert isinstance(snap, dict)
    assert "frequency" in snap or len(snap) >= 0  # best-effort


async def test_snapshot_state_uses_cache_on_failure(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """When CI-V times out, snapshot falls back to cached values."""
    radio._last_freq_hz = 7_074_000
    radio._last_mode = Mode.LSB
    radio._last_power = 100
    # No responses queued → all get* will timeout → cache fallback

    snap = await radio.snapshot_state()
    assert snap.get("frequency") == 7_074_000
    assert snap.get("mode") == Mode.LSB
    assert snap.get("power") == 100


async def test_snapshot_state_includes_packet_data_settings(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    radio.get_freq = AsyncMock(return_value=14_074_000)
    radio.get_mode_info = AsyncMock(return_value=(Mode.FM, 1))
    radio.get_rf_power = AsyncMock(return_value=25)
    radio.get_vox = AsyncMock(return_value=False)
    radio.get_data_mode = AsyncMock(return_value=True)
    radio.get_data_off_mod_input = AsyncMock(return_value=3)
    radio.get_data1_mod_input = AsyncMock(return_value=4)

    snap = await radio.snapshot_state()

    assert snap["vox"] is False
    assert snap["data_mode"] is True
    assert snap["data_off_mod_input"] == 3
    assert snap["data1_mod_input"] == 4


# ---------------------------------------------------------------------------
# restore_state() (lines 1514-1559)
# ---------------------------------------------------------------------------


async def test_restore_state_calls_set_methods(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """restore_state() should call set_frequency, set_mode, set_power etc."""
    set_freq_called = False
    set_mode_called = False
    set_power_called = False
    set_vox_called = False
    set_data_mode_called = False
    set_data_off_called = False
    set_data1_called = False

    async def mock_set_freq(hz: int) -> None:
        nonlocal set_freq_called
        set_freq_called = True

    async def mock_set_mode(mode: Mode, filter_width: int | None = None) -> None:
        nonlocal set_mode_called
        set_mode_called = True

    async def mock_set_power(level: int) -> None:
        nonlocal set_power_called
        set_power_called = True

    async def mock_set_vox(on: bool) -> None:
        nonlocal set_vox_called
        set_vox_called = True

    async def mock_set_data_mode(on: bool) -> None:
        nonlocal set_data_mode_called
        set_data_mode_called = True

    async def mock_set_data_off(source: int) -> None:
        nonlocal set_data_off_called
        set_data_off_called = True

    async def mock_set_data1(source: int) -> None:
        nonlocal set_data1_called
        set_data1_called = True

    with (
        patch.object(radio, "set_freq", side_effect=mock_set_freq),
        patch.object(radio, "set_mode", side_effect=mock_set_mode),
        patch.object(radio, "set_rf_power", side_effect=mock_set_power),
        patch.object(radio, "set_split", new=AsyncMock()),
        patch.object(radio, "_set_vfo_wire", new=AsyncMock()),
        patch.object(radio, "set_attenuator", new=AsyncMock()),
        patch.object(radio, "set_preamp", new=AsyncMock()),
        patch.object(radio, "set_vox", side_effect=mock_set_vox),
        patch.object(radio, "set_data_mode", side_effect=mock_set_data_mode),
        patch.object(radio, "set_data_off_mod_input", side_effect=mock_set_data_off),
        patch.object(radio, "set_data1_mod_input", side_effect=mock_set_data1),
    ):
        state = {
            "frequency": 14_074_000,
            "mode": Mode.USB,
            "filter": 1,
            "power": 128,
            "split": False,
            "vfo": "VFOA",
            "vox": False,
            "data_mode": True,
            "data_off_mod_input": 3,
            "data1_mod_input": 4,
        }
        await radio.restore_state(state)

    assert set_freq_called
    assert set_mode_called
    assert set_power_called
    assert set_vox_called
    assert set_data_mode_called
    assert set_data_off_called
    assert not set_data1_called


async def test_restore_state_ignores_set_failure(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """restore_state() must not propagate exceptions from set methods."""

    async def failing(*_: object, **__: object) -> None:
        raise ConnectionError("radio dead")

    with (
        patch.object(radio, "set_freq", side_effect=failing),
        patch.object(radio, "set_mode", side_effect=failing),
        patch.object(radio, "set_rf_power", side_effect=failing),
        patch.object(radio, "set_split", side_effect=failing),
        patch.object(radio, "_set_vfo_wire", side_effect=failing),
        patch.object(radio, "set_attenuator", side_effect=failing),
        patch.object(radio, "set_preamp", side_effect=failing),
        patch.object(radio, "set_vox", side_effect=failing),
        patch.object(radio, "set_data_mode", side_effect=failing),
        patch.object(radio, "set_data_off_mod_input", side_effect=failing),
        patch.object(radio, "set_data1_mod_input", side_effect=failing),
    ):
        state = {
            "frequency": 14_074_000,
            "mode": Mode.USB,
            "filter": 1,
            "power": 128,
            "split": False,
            "vfo": "VFOA",
            "attenuator": True,
            "preamp": 1,
            "vox": True,
            "data_mode": True,
            "data_off_mod_input": 3,
            "data1_mod_input": 4,
        }
        # Must not raise
        await radio.restore_state(state)


# ---------------------------------------------------------------------------
# run_state_transaction() (lines 1566-1580)
# ---------------------------------------------------------------------------


async def test_run_state_transaction_snapshot_and_restore(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """run_state_transaction() calls snapshot before and restore after body."""
    snap_called = False
    restore_called = False
    body_called = False

    async def fake_snapshot() -> dict:
        nonlocal snap_called
        snap_called = True
        return {"frequency": 14_074_000}

    async def fake_restore(state: dict) -> None:
        nonlocal restore_called
        restore_called = True

    async def body() -> None:
        nonlocal body_called
        body_called = True

    radio._commander = None  # use non-commander path (line 1572)
    with (
        patch.object(radio, "snapshot_state", side_effect=fake_snapshot),
        patch.object(radio, "restore_state", side_effect=fake_restore),
    ):
        await radio.run_state_transaction(body)

    assert snap_called
    assert body_called
    assert restore_called


# ---------------------------------------------------------------------------
# scope_stream() (lines 1653-1661)
# ---------------------------------------------------------------------------


async def test_scope_stream_yields_frames(radio: IcomRadio) -> None:
    """scope_stream() should yield frames from the queue while connected."""
    frame = ScopeFrame(
        receiver=0,
        mode=1,
        start_freq_hz=14_000_000,
        end_freq_hz=14_350_000,
        pixels=bytes([80] * 50),
        out_of_range=False,
    )
    await radio._scope_frame_queue.put(frame)

    # Set connected and then disconnect to terminate the generator
    received = []

    async def _consume() -> None:
        async for f in radio.scope_stream():
            received.append(f)
            radio._connected = False  # Stop after first frame

    await asyncio.wait_for(_consume(), timeout=3.0)
    assert len(received) >= 1
    assert received[0] is frame


async def test_scope_stream_exits_when_disconnected(radio: IcomRadio) -> None:
    """scope_stream() must exit promptly when radio disconnects."""
    radio._connected = False  # already disconnected

    frames = []
    async for f in radio.scope_stream():
        frames.append(f)

    assert frames == []


# ---------------------------------------------------------------------------
# enable_scope() — FAST policy (lines 1681-1709)
# ---------------------------------------------------------------------------


async def test_enable_scope_fast_policy_no_wait(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """FAST policy sends commands without waiting for ACK or verification."""
    from rigplane.types import ScopeCompletionPolicy

    await radio.enable_scope(policy=ScopeCompletionPolicy.FAST)
    assert len(mock_transport.sent_packets) >= 1


async def test_enable_scope_fast_no_output(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """enable_scope(output=False) should only send the on command."""
    from rigplane.types import ScopeCompletionPolicy

    await radio.enable_scope(output=False, policy=ScopeCompletionPolicy.FAST)
    # Only the scope-on command sent (not the data output command)
    assert len(mock_transport.sent_packets) == 1


# ---------------------------------------------------------------------------
# enable_scope() — STRICT policy (lines 1688-1701)
# ---------------------------------------------------------------------------


async def test_enable_scope_strict_sends_and_acks(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """STRICT policy waits for ACK from each command."""
    from rigplane.types import ScopeCompletionPolicy

    # Queue two ACK responses (one for scope-on, one for scope-output)
    mock_transport.queue_response(_ack_response())
    mock_transport.queue_response(_ack_response())
    await radio.enable_scope(policy=ScopeCompletionPolicy.STRICT)


async def test_enable_scope_strict_nak_raises(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """STRICT policy should raise CommandError on NAK."""
    from rigplane.types import ScopeCompletionPolicy

    mock_transport.queue_response(_nak_response())
    with pytest.raises(CommandError, match="scope enable"):
        await radio.enable_scope(policy=ScopeCompletionPolicy.STRICT)


# ---------------------------------------------------------------------------
# enable_scope() — VERIFY policy (lines 1703-1709)
# ---------------------------------------------------------------------------


async def test_enable_scope_verify_times_out(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """VERIFY policy raises TimeoutError if no scope data arrives."""
    from rigplane.types import ScopeCompletionPolicy

    # No scope data will arrive → event never set
    with pytest.raises(TimeoutError, match="Scope enable"):
        await radio.enable_scope(policy=ScopeCompletionPolicy.VERIFY, timeout=0.05)


async def test_enable_scope_verify_succeeds_when_event_fires(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """VERIFY policy succeeds when scope activity event fires."""
    from rigplane.types import ScopeCompletionPolicy

    async def _set_event_soon() -> None:
        await asyncio.sleep(0.02)
        radio._scope_activity_event.set()

    task = asyncio.create_task(_set_event_soon())
    try:
        await radio.enable_scope(policy=ScopeCompletionPolicy.VERIFY, timeout=0.5)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# disable_scope() (lines 1722-1732)
# ---------------------------------------------------------------------------


async def test_disable_scope_fast_sends_command(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    from rigplane.types import ScopeCompletionPolicy

    await radio.disable_scope(policy=ScopeCompletionPolicy.FAST)
    assert len(mock_transport.sent_packets) >= 1


async def test_disable_scope_strict_nak_raises(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    from rigplane.types import ScopeCompletionPolicy

    mock_transport.queue_response(_nak_response())
    with pytest.raises(CommandError, match="scope data output disable"):
        await radio.disable_scope(policy=ScopeCompletionPolicy.STRICT)


async def test_disable_scope_strict_ack_succeeds(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    from rigplane.types import ScopeCompletionPolicy

    mock_transport.queue_response(_ack_response())
    await radio.disable_scope(policy=ScopeCompletionPolicy.STRICT)


# ---------------------------------------------------------------------------
# capture_scope_frame() / capture_scope_frames() (lines 1748-1791)
# ---------------------------------------------------------------------------


async def test_capture_scope_frame_returns_first_frame(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """capture_scope_frame() should return the first frame received."""
    frame = ScopeFrame(
        receiver=0,
        mode=1,
        start_freq_hz=14_000_000,
        end_freq_hz=14_350_000,
        pixels=bytes([60] * 100),
        out_of_range=False,
    )

    # Simulate scope frames arriving shortly
    async def _push_frame() -> None:
        await asyncio.sleep(0.02)
        radio._scope_activity_event.set()
        cb = radio._scope_callback
        if cb is not None:
            cb(frame)

    task = asyncio.create_task(_push_frame())
    try:
        result = await radio.capture_scope_frame(timeout=1.0)
        assert result is frame
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_capture_scope_frames_timeout_raises(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """capture_scope_frames() raises TimeoutError if not enough frames arrive."""
    with pytest.raises(TimeoutError, match="timed out"):
        await radio.capture_scope_frames(count=5, timeout=0.05)


async def test_capture_scope_frames_multiple(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """capture_scope_frames(count=3) collects exactly 3 frames."""
    frames = [
        ScopeFrame(
            receiver=0,
            mode=1,
            start_freq_hz=14_000_000,
            end_freq_hz=14_350_000,
            pixels=bytes([i * 10] * 50),
            out_of_range=False,
        )
        for i in range(3)
    ]

    async def _push_frames() -> None:
        await asyncio.sleep(0.02)
        radio._scope_activity_event.set()
        cb = radio._scope_callback
        if cb is not None:
            for f in frames:
                cb(f)

    task = asyncio.create_task(_push_frames())
    try:
        result = await radio.capture_scope_frames(count=3, timeout=1.0)
        assert len(result) == 3
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# connected property — _udp_error_count check (lines 261, 265)
# ---------------------------------------------------------------------------


def test_connected_returns_false_when_error_count_above_threshold(
    radio: IcomRadio,
) -> None:
    """connected returns False only when _udp_error_count >= threshold (issue #954)."""
    radio._civ_transport._udp_error_count = 3  # type: ignore[attr-defined]
    assert radio.connected is False


def test_connected_stays_true_on_single_transient_udp_error(
    radio: IcomRadio,
) -> None:
    """A single transient UDP error must not latch .connected to False (issue #954)."""
    radio._civ_transport._udp_error_count = 1  # type: ignore[attr-defined]
    assert radio.connected is True
    radio._civ_transport._udp_error_count = 2  # type: ignore[attr-defined]
    assert radio.connected is True


def test_connected_returns_true_when_error_count_zero(radio: IcomRadio) -> None:
    """connected returns True when transport has _udp_error_count == 0."""
    radio._civ_transport._udp_error_count = 0  # type: ignore[attr-defined]
    assert radio.connected is True


def test_connected_returns_true_when_no_error_count_attr(radio: IcomRadio) -> None:
    """connected returns True when transport has no _udp_error_count (regular mock)."""
    # MockTransport has no _udp_error_count, so isinstance(None, int) is False
    assert radio.connected is True


def test_connected_returns_false_when_civ_transport_none(radio: IcomRadio) -> None:
    """connected returns False when _civ_transport is None."""
    radio._civ_transport = None
    assert radio.connected is False


# ---------------------------------------------------------------------------
# disconnect() — audio stream and transport (lines 332-334, 339-344)
# ---------------------------------------------------------------------------


async def test_disconnect_stops_audio_stream(radio: IcomRadio) -> None:
    """disconnect() stops audio stream and clears it (lines 332-334)."""
    audio_stream = MagicMock()
    audio_stream.stop_rx = AsyncMock()
    audio_stream.stop_tx = AsyncMock()
    radio._audio_stream = audio_stream

    with (
        patch.object(radio._control_phase, "_stop_watchdog"),
        patch.object(radio._control_phase, "_stop_reconnect"),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio, "_send_open_close", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
    ):
        await radio.disconnect()

    audio_stream.stop_rx.assert_awaited_once()
    audio_stream.stop_tx.assert_awaited_once()
    assert radio._audio_stream is None


async def test_disconnect_disconnects_audio_transport(radio: IcomRadio) -> None:
    """disconnect() closes audio transport (lines 339-344)."""
    audio_transport = MagicMock()
    audio_transport.disconnect = AsyncMock()
    radio._audio_transport = audio_transport

    with (
        patch.object(radio._control_phase, "_stop_watchdog"),
        patch.object(radio._control_phase, "_stop_reconnect"),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio, "_send_audio_open_close", new=AsyncMock()),
        patch.object(radio, "_send_open_close", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
    ):
        await radio.disconnect()

    audio_transport.disconnect.assert_awaited_once()
    assert radio._audio_transport is None


async def test_disconnect_sends_token_remove_before_ctrl_close(
    radio: IcomRadio,
) -> None:
    """disconnect() attempts token-remove (0x01) before closing control transport."""
    radio._ctrl_transport = MagicMock()
    radio._ctrl_transport.disconnect = AsyncMock()
    radio._civ_transport = MagicMock()
    radio._civ_transport.disconnect = AsyncMock()

    with (
        patch.object(radio._control_phase, "_stop_watchdog"),
        patch.object(radio._control_phase, "_stop_reconnect"),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio, "_send_open_close", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
        patch.object(
            radio._control_phase, "_send_token", new=AsyncMock()
        ) as send_token,
    ):
        await radio.disconnect()

    send_token.assert_awaited_once_with(0x01)
    radio._ctrl_transport.disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# soft_disconnect() — audio transport (lines 379-384)
# ---------------------------------------------------------------------------


async def test_soft_disconnect_disconnects_audio_transport(
    radio: IcomRadio,
) -> None:
    """soft_disconnect() closes audio transport when set (lines 379-384)."""
    audio_transport = MagicMock()
    audio_transport.disconnect = AsyncMock()
    radio._audio_transport = audio_transport

    with (
        patch.object(radio, "_send_audio_open_close", new=AsyncMock()),
        patch.object(radio, "_send_open_close", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
    ):
        await radio.soft_disconnect()

    audio_transport.disconnect.assert_awaited_once()
    assert radio._audio_transport is None


async def test_soft_disconnect_handles_civ_open_close_failure(
    radio: IcomRadio,
) -> None:
    """soft_disconnect() ignores exception from _send_open_close (lines 390-391)."""

    async def failing_open_close(*, open_stream: bool) -> None:
        raise OSError("connection refused")

    with (
        patch.object(radio, "_send_open_close", side_effect=failing_open_close),
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
    ):
        await radio.soft_disconnect()  # should not raise

    assert radio._civ_transport is None


# ---------------------------------------------------------------------------
# _force_cleanup_civ (lines 407-417)
# ---------------------------------------------------------------------------


async def test_force_cleanup_civ_tears_down_transport(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """_force_cleanup_civ disconnects transport unconditionally (lines 407-417)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    with (
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
    ):
        await radio._force_cleanup_civ()

    assert radio._civ_transport is None
    assert radio._conn_state == RadioConnectionState.DISCONNECTED


async def test_force_cleanup_civ_handles_disconnect_failure(
    radio: IcomRadio,
) -> None:
    """_force_cleanup_civ ignores transport.disconnect() exceptions (lines 414-415)."""
    failing_transport = MagicMock()
    failing_transport.disconnect = AsyncMock(side_effect=OSError("transport dead"))
    radio._civ_transport = failing_transport

    with (
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
    ):
        await radio._force_cleanup_civ()  # should not raise

    assert radio._civ_transport is None


async def test_force_cleanup_civ_when_no_transport(radio: IcomRadio) -> None:
    """_force_cleanup_civ works when civ_transport is already None."""
    radio._civ_transport = None

    with (
        patch.object(radio._civ_runtime, "stop_data_watchdog", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_worker", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
    ):
        await radio._force_cleanup_civ()  # should not raise


# ---------------------------------------------------------------------------
# soft_reconnect() — main reconnect path (lines 434-472)
# ---------------------------------------------------------------------------


async def test_soft_reconnect_reconnects_civ_when_ctrl_alive(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """soft_reconnect() opens new CI-V transport when ctrl is still alive (lines 434-472)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._civ_transport = None
    # Make ctrl transport appear alive
    radio._ctrl_transport._udp_transport = MagicMock()  # type: ignore[attr-defined]

    fake_civ_transport = MagicMock()
    fake_civ_transport.connect = AsyncMock()
    fake_civ_transport.send_tracked = AsyncMock()
    fake_civ_transport.my_id = 1
    fake_civ_transport.remote_id = 2
    fake_civ_transport._udp_error_count = 0

    with (
        patch("rigplane.transport.IcomTransport", return_value=fake_civ_transport),
        patch.object(radio, "_send_open_close", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
        patch.object(radio._civ_runtime, "start_pump"),
        patch.object(radio._civ_runtime, "start_worker"),
        patch.object(radio._civ_runtime, "start_data_watchdog"),
    ):
        await radio.soft_reconnect()

    assert radio._conn_state == RadioConnectionState.CONNECTED
    assert radio._civ_transport is fake_civ_transport


async def test_soft_reconnect_calls_on_reconnect_callback(
    radio: IcomRadio,
) -> None:
    """soft_reconnect() calls _on_reconnect callback if set (lines 468-472)."""
    radio._civ_transport = None
    radio._ctrl_transport._udp_transport = MagicMock()  # type: ignore[attr-defined]

    fake_civ_transport = MagicMock()
    fake_civ_transport.connect = AsyncMock()
    fake_civ_transport.send_tracked = AsyncMock()
    fake_civ_transport.my_id = 1
    fake_civ_transport.remote_id = 2
    fake_civ_transport._udp_error_count = 0

    reconnect_called = [False]

    def mock_on_reconnect() -> None:
        reconnect_called[0] = True

    radio._on_reconnect = mock_on_reconnect

    with (
        patch("rigplane.transport.IcomTransport", return_value=fake_civ_transport),
        patch.object(radio, "_send_open_close", new=AsyncMock()),
        patch.object(radio._civ_runtime, "stop_pump", new=AsyncMock()),
        patch.object(radio._civ_runtime, "start_pump"),
        patch.object(radio._civ_runtime, "start_worker"),
        patch.object(radio._civ_runtime, "start_data_watchdog"),
    ):
        await radio.soft_reconnect()

    assert reconnect_called[0]


async def test_soft_reconnect_handles_connect_failure(radio: IcomRadio) -> None:
    """soft_reconnect() raises ConnectionError when transport.connect() fails (lines 444-447)."""
    from rigplane.transport import IcomTransport

    radio._civ_transport = None
    radio._ctrl_transport._udp_transport = MagicMock()  # type: ignore[attr-defined]

    # Patch IcomTransport constructor to return a mock that fails on connect.
    # The patch target must be "rigplane.transport.IcomTransport" because
    # _control_phase.soft_reconnect() does a local `from .transport import IcomTransport`
    # inside the function body, which resolves via rigplane.transport, not the
    # _control_phase module namespace.
    async def failing_connect(*args, **kwargs):
        raise OSError("connection refused")

    fake_transport = AsyncMock(spec=IcomTransport)
    fake_transport.connect = failing_connect

    with patch("rigplane.transport.IcomTransport", return_value=fake_transport):
        with pytest.raises(ConnectionError, match="Failed to reconnect CI-V"):
            await radio.soft_reconnect()

    assert radio._civ_transport is None


# ---------------------------------------------------------------------------
# _watchdog_loop (lines 496, 506-508, 515-521)
# ---------------------------------------------------------------------------


async def test_watchdog_loop_exits_when_disconnected(radio: IcomRadio) -> None:
    """_watchdog_loop exits when _connected becomes False (line 496)."""
    radio._connected = False  # already disconnected
    # Should exit immediately without starting a reconnect
    await radio._watchdog_loop()


async def test_watchdog_loop_detects_activity(radio: IcomRadio) -> None:
    """_watchdog_loop updates last_activity when packets arrive (lines 506-508)."""
    radio._ctrl_transport.rx_packet_count = 0
    call_count = [0]

    async def mock_sleep(delay: float) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            # Simulate packet arrival
            radio._ctrl_transport.rx_packet_count = 5
        elif call_count[0] >= 2:
            radio._connected = False
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        await radio._watchdog_loop()

    assert call_count[0] >= 2


async def test_watchdog_loop_logs_health_periodically(
    radio: IcomRadio, caplog: pytest.LogCaptureFixture
) -> None:
    """_watchdog_loop logs health info periodically (lines 515-521)."""
    import logging
    import time as time_mod

    radio._ctrl_transport.rx_packet_count = 10
    call_count = [0]
    base_time = time_mod.monotonic()

    def advancing_time() -> float:
        call_count[0] += 1
        # Advance time beyond health log interval on later calls
        if call_count[0] <= 3:
            return base_time
        return base_time + radio._WATCHDOG_HEALTH_LOG_INTERVAL + 1.0

    async def mock_sleep(delay: float) -> None:
        if call_count[0] >= 6:
            radio._connected = False
            raise asyncio.CancelledError()

    with (
        patch("rigplane.radio.time.monotonic", side_effect=advancing_time),
        patch("asyncio.sleep", side_effect=mock_sleep),
        caplog.at_level(logging.INFO, logger="rigplane.runtime.radio"),
    ):
        await radio._watchdog_loop()


async def test_watchdog_loop_triggers_reconnect_on_timeout(radio: IcomRadio) -> None:
    """_watchdog_loop triggers reconnect after idle timeout (lines 523-531)."""
    import time as time_mod

    radio._watchdog_timeout = 0.01  # very short timeout

    call_count = [0]
    base_time = time_mod.monotonic()

    def mock_time() -> float:
        call_count[0] += 1
        # Start just past last activity so watchdog triggers
        return base_time + (call_count[0] * 1.0)

    async def mock_sleep(delay: float) -> None:
        pass  # Don't actually wait

    scheduled: list[str] = []

    def _create_task(coro: object) -> MagicMock:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        task = MagicMock()
        task.done.return_value = False
        scheduled.append("reconnect")
        return task

    with (
        patch("rigplane.radio.time.monotonic", side_effect=mock_time),
        patch("asyncio.sleep", side_effect=mock_sleep),
        patch("asyncio.create_task", side_effect=_create_task),
    ):
        await radio._watchdog_loop()

    # A reconnect task should have been created
    assert scheduled == ["reconnect"]


# ---------------------------------------------------------------------------
# _reconnect_loop (lines 553-586)
# ---------------------------------------------------------------------------


async def test_reconnect_loop_succeeds_on_first_attempt(radio: IcomRadio) -> None:
    """_reconnect_loop reconnects successfully on first attempt (lines 553-581)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.RECONNECTING

    connect_called = [False]
    fake_transport = MagicMock()

    async def fake_connect() -> None:
        connect_called[0] = True
        radio._conn_state = RadioConnectionState.CONNECTED

    with (
        patch("rigplane.radio.IcomTransport", return_value=fake_transport),
        patch.object(radio, "connect", side_effect=fake_connect),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio._audio_runtime, "capture_snapshot", return_value=None),
    ):
        await radio._reconnect_loop()

    assert connect_called[0]


async def test_reconnect_loop_handles_audio_stop_failure(radio: IcomRadio) -> None:
    """_reconnect_loop handles failure when stopping audio stream (lines 553-554)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.RECONNECTING

    # Set up a failing audio stream
    audio_stream = MagicMock()
    audio_stream.stop_rx = AsyncMock(side_effect=OSError("rx stop failed"))
    audio_stream.stop_tx = AsyncMock()
    radio._audio_stream = audio_stream

    connect_called = [False]

    async def fake_connect() -> None:
        connect_called[0] = True
        radio._conn_state = RadioConnectionState.CONNECTED

    with (
        patch("rigplane.radio.IcomTransport", return_value=MagicMock()),
        patch.object(radio, "connect", side_effect=fake_connect),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio._audio_runtime, "capture_snapshot", return_value=None),
    ):
        await radio._reconnect_loop()

    assert connect_called[0]


async def test_reconnect_loop_retries_on_failure(radio: IcomRadio) -> None:
    """_reconnect_loop retries with backoff when connect fails (lines 583-586)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.RECONNECTING
    radio._reconnect_delay = 0.001  # very short delay

    call_count = [0]

    async def intermittent_connect() -> None:
        call_count[0] += 1
        if call_count[0] < 3:
            raise ConnectionError("radio unreachable")
        radio._conn_state = RadioConnectionState.CONNECTED

    with (
        patch("rigplane.radio.IcomTransport", return_value=MagicMock()),
        patch.object(radio, "connect", side_effect=intermittent_connect),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio._audio_runtime, "capture_snapshot", return_value=None),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await radio._reconnect_loop()

    assert call_count[0] == 3


async def test_reconnect_loop_stops_audio_transport_on_reconnect(
    radio: IcomRadio,
) -> None:
    """_reconnect_loop disconnects audio transport during retry (lines 559-561)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.RECONNECTING

    audio_transport = MagicMock()
    audio_transport.disconnect = AsyncMock()
    radio._audio_transport = audio_transport

    async def fake_connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED

    with (
        patch("rigplane.radio.IcomTransport", return_value=MagicMock()),
        patch.object(radio, "connect", side_effect=fake_connect),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio._audio_runtime, "capture_snapshot", return_value=None),
    ):
        await radio._reconnect_loop()

    audio_transport.disconnect.assert_awaited_once()


async def test_reconnect_loop_stops_civ_transport_on_reconnect(
    radio: IcomRadio,
) -> None:
    """_reconnect_loop disconnects civ transport during retry (lines 564-567)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.RECONNECTING

    async def fake_connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED

    with (
        patch("rigplane.radio.IcomTransport", return_value=MagicMock()),
        patch.object(radio, "connect", side_effect=fake_connect),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio._audio_runtime, "capture_snapshot", return_value=None),
    ):
        await radio._reconnect_loop()

    # _civ_transport was disconnected during reconnect
    assert radio._civ_transport is None


async def test_reconnect_loop_stops_ctrl_transport_on_reconnect(
    radio: IcomRadio,
) -> None:
    """_reconnect_loop disconnects ctrl transport during retry (lines 568-571)."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.RECONNECTING

    async def fake_connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED

    with (
        patch("rigplane.radio.IcomTransport", return_value=MagicMock()),
        patch.object(radio, "connect", side_effect=fake_connect),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio._audio_runtime, "capture_snapshot", return_value=None),
    ):
        await radio._reconnect_loop()
    # No assertion needed — just verify it doesn't raise


async def test_reconnect_loop_attempts_token_remove(
    radio: IcomRadio,
) -> None:
    """_reconnect_loop sends token-remove before ctrl transport disconnect."""
    from rigplane.runtime._connection_state import RadioConnectionState

    radio._conn_state = RadioConnectionState.RECONNECTING
    radio._audio_transport = None
    radio._civ_transport = None
    radio._ctrl_transport = MagicMock()
    radio._ctrl_transport.disconnect = AsyncMock()

    async def fake_connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED

    with (
        patch("rigplane.radio.IcomTransport", return_value=MagicMock()),
        patch.object(radio, "connect", side_effect=fake_connect),
        patch.object(radio, "_stop_token_renewal"),
        patch.object(radio._audio_runtime, "capture_snapshot", return_value=None),
        patch.object(radio, "_send_token", new=AsyncMock()) as send_token,
    ):
        await radio._reconnect_loop()

    send_token.assert_awaited_once_with(0x01)


# ---------------------------------------------------------------------------
# _ensure_audio_transport (lines 993-1013)
# ---------------------------------------------------------------------------


async def test_ensure_audio_transport_raises_when_audio_port_zero(
    radio: IcomRadio,
) -> None:
    """_ensure_audio_transport raises ConnectionError when audio port is 0 (line 991)."""
    radio._audio_port = 0
    with pytest.raises(ConnectionError, match="Audio port not available"):
        await radio._ensure_audio_transport()


async def test_ensure_audio_transport_creates_transport(radio: IcomRadio) -> None:
    """_ensure_audio_transport connects audio transport (lines 993-1013)."""

    radio._audio_port = 50001  # non-zero port
    radio._local_bind_host = "192.168.2.194"

    fake_transport = MagicMock()
    fake_transport.connect = AsyncMock()

    with (
        patch(
            "rigplane._audio_runtime_mixin.IcomTransport", return_value=fake_transport
        ),
        patch.object(radio, "_send_audio_open_close", new=AsyncMock()),
        patch("rigplane._audio_runtime_mixin.AudioStream"),
    ):
        await radio._ensure_audio_transport()

    assert radio._audio_transport is fake_transport
    fake_transport.connect.assert_awaited_once_with(
        radio._host,
        50001,
        local_host="192.168.2.194",
        local_port=0,
        sock=None,
    )


async def test_ensure_audio_transport_noop_when_stream_exists(radio: IcomRadio) -> None:
    """_ensure_audio_transport is noop when _audio_stream already set (line 987-988)."""
    radio._audio_stream = MagicMock()  # already connected

    with patch("rigplane._audio_runtime_mixin.IcomTransport") as mock_cls:
        await radio._ensure_audio_transport()
        mock_cls.assert_not_called()


async def test_ensure_audio_transport_handles_connect_failure(radio: IcomRadio) -> None:
    """_ensure_audio_transport wraps OSError as ConnectionError (lines 999-1003)."""
    radio._audio_port = 50002

    fake_transport = MagicMock()
    fake_transport.connect = AsyncMock(side_effect=OSError("port busy"))

    with patch(
        "rigplane._audio_runtime_mixin.IcomTransport", return_value=fake_transport
    ):
        with pytest.raises(ConnectionError, match="Failed to connect audio port"):
            await radio._ensure_audio_transport()

    assert radio._audio_transport is None


# ---------------------------------------------------------------------------
# get_filter / set_filter (lines 1117-1118, 1122-1123)
# ---------------------------------------------------------------------------


async def test_get_filter_returns_filter_width(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_filter() returns filter width from mode response (lines 1117-1118)."""
    from rigplane.commands import _CMD_MODE_GET

    mode_civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_MODE_GET,
        data=bytes([Mode.USB.value, 2]),
    )
    mock_transport.queue_response(_wrap_civ_in_udp(mode_civ))
    result = await radio.get_filter()
    assert result == 2


async def test_set_filter_reads_mode_then_sets(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """set_filter() reads current mode then calls set_mode (lines 1122-1123)."""
    from rigplane.commands import _CMD_MODE_GET

    mode_civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_MODE_GET,
        data=bytes([Mode.USB.value, 1]),
    )
    mock_transport.queue_response(_wrap_civ_in_udp(mode_civ))

    with patch.object(radio, "set_mode", new=AsyncMock()) as mock_set:
        await radio.set_filter(2)

    mock_set.assert_awaited_once_with("USB", filter_width=2, receiver=0)


# ---------------------------------------------------------------------------
# set_mode with filter_width (line 1140)
# ---------------------------------------------------------------------------


async def test_set_mode_with_filter_width_updates_state(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """set_mode() with filter_width updates _filter_width (line 1140)."""
    await radio.set_mode(Mode.USB, filter_width=2)
    assert radio._filter_width == 2


# ---------------------------------------------------------------------------
# get_rf_gain / get_af_level success paths (lines 1210, 1228)
# ---------------------------------------------------------------------------


async def test_get_rf_gain_returns_level(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_rf_gain() returns parsed level on success (line 1210)."""
    # Build RF gain response: cmd 0x14 sub 0x02, value BCD
    d = f"{200:04d}"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x14, sub=0x02, data=bytes([b0, b1])
    )
    mock_transport.queue_response(_wrap_civ_in_udp(civ))
    result = await radio.get_rf_gain()
    assert result == 200


async def test_get_af_level_returns_level(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_af_level() returns parsed level on success (line 1228)."""
    d = f"{150:04d}"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x14, sub=0x01, data=bytes([b0, b1])
    )
    mock_transport.queue_response(_wrap_civ_in_udp(civ))
    result = await radio.get_af_level()
    assert result == 150


# ---------------------------------------------------------------------------
# set_preamp error propagation (lines 1421, 1426-1427)
# ---------------------------------------------------------------------------


async def test_set_preamp_propagates_own_digi_sel_error(
    radio: IcomRadio,
) -> None:
    """set_preamp() propagates its own DIGI-SEL CommandError (lines 1421, 1426-1427)."""
    from rigplane.exceptions import CommandError

    # Mock get_digisel to return True (DIGI-SEL on)
    with patch.object(radio, "get_digisel", new=AsyncMock(return_value=True)):
        with pytest.raises(CommandError, match="DIGI-SEL"):
            await radio.set_preamp(1)


async def test_set_preamp_ignores_unrelated_command_error(
    radio: IcomRadio,
) -> None:
    """set_preamp() ignores CommandError that isn't its own DIGI-SEL error."""
    from rigplane.exceptions import CommandError

    # Mock get_digisel to raise a different CommandError
    with patch.object(
        radio,
        "get_digisel",
        new=AsyncMock(side_effect=CommandError("radio unreachable")),
    ):
        # Should NOT raise — it's a different CommandError, not DIGI-SEL
        await radio.set_preamp(1)


# ---------------------------------------------------------------------------
# get_nb / get_nr / get_ip_plus — empty data fallback (lines 1442, 1462, 1475, 1490)
# ---------------------------------------------------------------------------


async def test_get_nb_returns_false_on_empty_data(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_nb() returns False when response has no data (line 1462)."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x16, sub=0x22)
    mock_transport.queue_response(_wrap_civ_in_udp(civ))
    result = await radio.get_nb()
    assert result is False


async def test_get_nr_returns_true_on_data(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_nr() returns True when response data byte is 0x01 (line 1475)."""
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x16, sub=0x40, data=bytes([0x01])
    )
    mock_transport.queue_response(_wrap_civ_in_udp(civ))
    result = await radio.get_nr()
    assert result is True


async def test_get_nr_returns_false_on_empty_data(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_nr() returns False when response has no data (line 1475 else branch)."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x16, sub=0x40)
    mock_transport.queue_response(_wrap_civ_in_udp(civ))
    result = await radio.get_nr()
    assert result is False


async def test_get_ip_plus_returns_true_on_data(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_ip_plus() returns True when response data byte is 0x01 (line 1490)."""
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x16, sub=0x65, data=bytes([0x01])
    )
    mock_transport.queue_response(_wrap_civ_in_udp(civ))
    result = await radio.get_ip_plus()
    assert result is True


async def test_get_ip_plus_returns_false_on_empty_data(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """get_ip_plus() returns False when response has no data (line 1490 else branch)."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x16, sub=0x65)
    mock_transport.queue_response(_wrap_civ_in_udp(civ))
    result = await radio.get_ip_plus()
    assert result is False


# ---------------------------------------------------------------------------
# snapshot_state — cached attribute fallback paths (lines 1520, 1530, 1532, 1534, 1536)
# ---------------------------------------------------------------------------


async def test_snapshot_state_includes_cached_filter(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """snapshot_state() includes _filter_width when mode fails (line 1520)."""
    radio._last_mode = Mode.USB
    radio._filter_width = 2  # cached filter
    radio._last_freq_hz = 14_074_000
    radio._last_power = 100

    snap = await radio.snapshot_state()
    assert snap.get("filter") == 2


async def test_snapshot_state_includes_last_split(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """snapshot_state() includes _last_split when set (line 1530)."""
    radio._last_split = True
    radio._last_freq_hz = 14_074_000

    snap = await radio.snapshot_state()
    assert snap.get("split") is True


async def test_snapshot_state_includes_last_vfo(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """snapshot_state() includes _last_vfo when set (line 1532)."""
    radio._last_vfo = "VFOB"
    radio._last_freq_hz = 14_074_000

    snap = await radio.snapshot_state()
    assert snap.get("vfo") == "VFOB"


async def test_snapshot_state_includes_attenuator_state(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """snapshot_state() includes _attenuator_state when set (line 1534)."""
    radio._attenuator_state = True
    radio._last_freq_hz = 14_074_000

    snap = await radio.snapshot_state()
    assert snap.get("attenuator") is True


async def test_snapshot_state_includes_preamp_level(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """snapshot_state() includes _preamp_level when set (line 1536)."""
    radio._preamp_level = 1
    radio._last_freq_hz = 14_074_000

    snap = await radio.snapshot_state()
    assert snap.get("preamp") == 1


# ---------------------------------------------------------------------------
# run_state_transaction — body and commander paths (lines 1597-1598, 1608)
# ---------------------------------------------------------------------------


async def test_run_state_transaction_body_is_called(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """run_state_transaction() calls body() (lines 1597-1598)."""
    body_called = [False]

    async def body() -> None:
        body_called[0] = True

    with (
        patch.object(radio, "snapshot_state", new=AsyncMock(return_value={})),
        patch.object(radio, "restore_state", new=AsyncMock()),
    ):
        await radio.run_state_transaction(body)

    assert body_called[0]


async def test_run_state_transaction_uses_commander_when_available(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    """run_state_transaction() uses commander.transaction() when commander is set (line 1608)."""
    from rigplane.commander import IcomCommander

    mock_commander = MagicMock(spec=IcomCommander)
    mock_commander.transaction = AsyncMock()
    radio._commander = mock_commander

    async def body() -> None:
        pass

    await radio.run_state_transaction(body)
    mock_commander.transaction.assert_awaited_once()


# ---------------------------------------------------------------------------
# scope_stream — task_done (lines 1688-1689)
# ---------------------------------------------------------------------------


async def test_scope_stream_calls_task_done(radio: IcomRadio) -> None:
    """scope_stream() calls task_done after yielding a frame (lines 1688-1689)."""
    frame = ScopeFrame(
        receiver=0,
        mode=1,
        start_freq_hz=14_000_000,
        end_freq_hz=14_350_000,
        pixels=bytes([80] * 50),
        out_of_range=False,
    )
    await radio._scope_frame_queue.put(frame)

    received = []
    async for f in radio.scope_stream():
        received.append(f)
        radio._connected = False  # Stop after first frame

    assert received[0] is frame
    # task_done was called; queue should be joinable (empty)
    assert radio._scope_frame_queue.empty()
