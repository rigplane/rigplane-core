"""Additional coverage tests for icom_lan.sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from icom_lan.runtime._connection_state import RadioConnectionState
from icom_lan.sync import IcomRadio


def _radio() -> IcomRadio:
    r = IcomRadio("127.0.0.1")
    r._radio.connect = AsyncMock()
    r._radio.disconnect = AsyncMock()
    return r


def test_connect_disconnect_and_context_manager() -> None:
    r = _radio()
    r.connect()
    r.disconnect()
    r._radio.connect.assert_awaited_once()
    r._radio.disconnect.assert_awaited_once()
    r._loop.close()

    r2 = _radio()
    with r2 as entered:
        assert entered is r2
    assert r2._loop.is_closed()
    r2._radio.connect.assert_awaited_once()
    r2._radio.disconnect.assert_awaited_once()


def test_sync_wrappers_delegate_and_return_values() -> None:
    r = _radio()
    # Make radio appear connected so send_civ_raw / recovery paths do not raise
    r._radio._ctrl_transport = MagicMock()
    r._radio._ctrl_transport._udp_transport = MagicMock()
    r._radio._civ_transport = MagicMock()
    r._radio._conn_state = RadioConnectionState.CONNECTED
    r._radio.get_freq = AsyncMock(return_value=7_100_000)
    r._radio.get_mode = AsyncMock(return_value=("USB", 2))
    r._radio.get_mode_info = AsyncMock(return_value=("USB", 2))
    r._radio.get_filter = AsyncMock(return_value=2)
    r._radio.get_data_mode = AsyncMock(return_value=True)
    r._radio.get_rf_power = AsyncMock(return_value=200)
    r._radio.get_s_meter = AsyncMock(return_value=99)
    r._radio.get_swr = AsyncMock(return_value=10)
    r._radio.get_attenuator_level = AsyncMock(return_value=18)
    r._radio.get_attenuator = AsyncMock(return_value=True)
    r._radio.get_preamp = AsyncMock(return_value=1)
    r._radio.get_digisel = AsyncMock(return_value=False)
    r._radio.get_data_off_mod_input = AsyncMock(return_value=3)
    r._radio.get_data1_mod_input = AsyncMock(return_value=4)
    r._radio.get_vox = AsyncMock(return_value=True)
    r._radio.snapshot_state = AsyncMock(return_value={"freq": 7100000})

    r._radio.set_freq = AsyncMock()
    r._radio.set_filter = AsyncMock()
    r._radio.set_mode = AsyncMock()
    r._radio.set_data_mode = AsyncMock()
    r._radio.set_rf_power = AsyncMock()
    r._radio.set_ptt = AsyncMock()
    r._radio.equalize_main_sub = AsyncMock()
    r._radio.swap_main_sub = AsyncMock()
    r._radio.set_split = AsyncMock()
    r._radio.set_attenuator_level = AsyncMock()
    r._radio.set_attenuator = AsyncMock()
    r._radio.set_preamp = AsyncMock()
    r._radio.set_digisel = AsyncMock()
    r._radio.set_squelch = AsyncMock()
    r._radio.set_data_off_mod_input = AsyncMock()
    r._radio.set_data1_mod_input = AsyncMock()
    r._radio.set_vox = AsyncMock()
    r._radio.restore_state = AsyncMock()
    r._radio.send_cw_text = AsyncMock()
    r._radio.stop_cw_text = AsyncMock()
    r._radio.set_powerstat = AsyncMock()
    r._radio.enable_scope = AsyncMock()
    r._radio.set_scope_mode = AsyncMock()
    r._radio.set_scope_span = AsyncMock()

    assert r.get_freq() == 7_100_000
    assert r.get_mode() == ("USB", 2)
    assert r.get_mode_info() == ("USB", 2)
    assert r.get_filter() == 2
    assert r.get_data_mode() is True
    assert r.get_rf_power() == 200
    assert r.get_s_meter() == 99
    assert r.get_swr() == 10
    assert r.get_attenuator_level() == 18
    assert r.get_attenuator() is True
    assert r.get_preamp() == 1
    assert r.get_digisel() is False
    assert r.get_data_off_mod_input() == 3
    assert r.get_data1_mod_input() == 4
    assert r.get_vox() is True
    assert r.snapshot_state() == {"freq": 7100000}

    r.set_freq(7100000)
    r.set_filter(2)
    r.set_mode("LSB", 1)
    r.set_data_mode(True)
    r.set_rf_power(150)
    r.set_ptt(True)
    r.vfo_equalize()
    r.vfo_exchange()
    r.set_split(True)
    r.set_attenuator_level(18)
    r.set_attenuator(True)
    r.set_preamp(2)
    r.set_digisel(True)
    r.set_squelch(100, receiver=1)
    r.set_data_off_mod_input(2)
    r.set_data1_mod_input(1)
    r.set_vox(True)
    r.restore_state({"freq": 7000000})
    r.send_cw_text("TEST")
    r.stop_cw_text()
    r.power_control(False)
    r.enable_scope(output=False, policy="fast", timeout=1.5)
    r.set_scope_mode(3)
    r.set_scope_span(6)

    r._radio.set_freq.assert_awaited_once_with(7100000)
    r._radio.set_filter.assert_awaited_once_with(2)
    r._radio.set_mode.assert_awaited_once_with("LSB", 1)
    r._radio.set_data_mode.assert_awaited_once_with(True, receiver=0)
    r._radio.set_rf_power.assert_awaited_once_with(150)
    r._radio.set_ptt.assert_awaited_once_with(True)
    # IC-7610 (default profile, receiver_count=2) → canonical dual-RX methods
    r._radio.equalize_main_sub.assert_awaited_once()
    r._radio.swap_main_sub.assert_awaited_once()
    r._radio.set_split.assert_awaited_once_with(True)
    r._radio.set_attenuator_level.assert_awaited_once_with(18)
    r._radio.set_attenuator.assert_awaited_once_with(True)
    r._radio.set_preamp.assert_awaited_once_with(2)
    r._radio.set_digisel.assert_awaited_once_with(True)
    r._radio.set_squelch.assert_awaited_once_with(100, receiver=1)
    r._radio.set_data_off_mod_input.assert_awaited_once_with(2)
    r._radio.set_data1_mod_input.assert_awaited_once_with(1)
    r._radio.set_vox.assert_awaited_once_with(True)
    r._radio.restore_state.assert_awaited_once_with({"freq": 7000000})
    r._radio.send_cw_text.assert_awaited_once_with("TEST")
    r._radio.stop_cw_text.assert_awaited_once()
    r._radio.set_powerstat.assert_awaited_once_with(False)
    r._radio.enable_scope.assert_awaited_once_with(
        output=False, policy="fast", timeout=1.5
    )
    r._radio.set_scope_mode.assert_awaited_once_with(3)
    r._radio.set_scope_span.assert_awaited_once_with(6)
    r._loop.close()


def test_vfo_dispatch_single_rx_uses_ab_methods() -> None:
    """On single-RX profiles (e.g. IC-7300, addr=0x94), ``vfo_equalize`` /
    ``vfo_exchange`` route to ``equalize_vfo_ab(0)`` / ``swap_vfo_ab(0)``.
    """
    r = IcomRadio("127.0.0.1", radio_addr=0x94)  # IC-7300 → receiver_count=1
    r._radio.connect = AsyncMock()
    r._radio.disconnect = AsyncMock()
    # Make radio appear connected so guards do not raise
    r._radio._ctrl_transport = MagicMock()
    r._radio._ctrl_transport._udp_transport = MagicMock()
    r._radio._civ_transport = MagicMock()
    r._radio._conn_state = RadioConnectionState.CONNECTED
    r._radio.equalize_vfo_ab = AsyncMock()
    r._radio.swap_vfo_ab = AsyncMock()
    # Canonical dual-RX methods must NOT be touched on a single-RX rig
    r._radio.equalize_main_sub = AsyncMock()
    r._radio.swap_main_sub = AsyncMock()

    assert r._radio.profile.receiver_count == 1
    r.vfo_equalize()
    r.vfo_exchange()

    r._radio.equalize_vfo_ab.assert_awaited_once_with(0)
    r._radio.swap_vfo_ab.assert_awaited_once_with(0)
    r._radio.equalize_main_sub.assert_not_awaited()
    r._radio.swap_main_sub.assert_not_awaited()
    r._loop.close()


def test_audio_wrappers_canonical_only() -> None:
    r = _radio()

    def cb(_pkt: object) -> None:
        return None

    r._radio.start_audio_rx_opus = AsyncMock()
    r._radio.stop_audio_rx_opus = AsyncMock()
    r._radio.start_audio_tx_opus = AsyncMock()
    r._radio.push_audio_tx_opus = AsyncMock()
    r._radio.stop_audio_tx_opus = AsyncMock()

    r.start_audio_rx_opus(cb, jitter_depth=7)
    r.stop_audio_rx_opus()
    r.start_audio_tx_opus()
    r.push_audio_tx_opus(b"\xaa\xbb")
    r.stop_audio_tx_opus()

    r._radio.start_audio_rx_opus.assert_awaited_once_with(cb, jitter_depth=7)
    r._radio.stop_audio_rx_opus.assert_awaited_once()
    r._radio.start_audio_tx_opus.assert_awaited_once()
    r._radio.push_audio_tx_opus.assert_awaited_once_with(b"\xaa\xbb")
    r._radio.stop_audio_tx_opus.assert_awaited_once()
    r._loop.close()


@pytest.mark.parametrize(
    "name",
    [
        "start_audio_rx",
        "stop_audio_rx",
        "start_audio_tx",
        "push_audio_tx",
        "stop_audio_tx",
    ],
)
def test_removed_audio_aliases_raise_attribute_error(name: str) -> None:
    """Sync aliases removed in #1111 must not exist on icom_lan.sync.IcomRadio."""
    r = _radio()
    try:
        assert not hasattr(r, name)
    finally:
        r._loop.close()


def test_sync_get_alc_meter_returns_int() -> None:
    """``sync.IcomRadio.get_alc_meter`` returns the raw 0-255 BCD value
    from the async ``MetersCapable.get_alc_meter`` (refs #1226)."""
    r = _radio()
    r._radio._ctrl_transport = MagicMock()
    r._radio._ctrl_transport._udp_transport = MagicMock()
    r._radio._civ_transport = MagicMock()
    r._radio._conn_state = RadioConnectionState.CONNECTED
    r._radio.get_alc_meter = AsyncMock(return_value=128)

    try:
        result = r.get_alc_meter()
        assert result == 128
        assert isinstance(result, int)
        r._radio.get_alc_meter.assert_awaited_once()
    finally:
        r._loop.close()


def test_sync_get_swr_meter_returns_int() -> None:
    """``sync.IcomRadio.get_swr_meter`` returns the raw 0-255 BCD value
    from the async ``MetersCapable.get_swr_meter`` (refs #1183)."""
    r = _radio()
    r._radio._ctrl_transport = MagicMock()
    r._radio._ctrl_transport._udp_transport = MagicMock()
    r._radio._civ_transport = MagicMock()
    r._radio._conn_state = RadioConnectionState.CONNECTED
    r._radio.get_swr_meter = AsyncMock(return_value=120)

    try:
        result = r.get_swr_meter()
        assert result == 120
        assert isinstance(result, int)
        r._radio.get_swr_meter.assert_awaited_once()
    finally:
        r._loop.close()


def test_sync_get_swr_returns_float() -> None:
    """Regression guard for #1177: ``sync.IcomRadio.get_swr`` returns a
    calibrated float (>= 1.0), not the raw 0-255 BCD reading."""
    r = _radio()
    r._radio._ctrl_transport = MagicMock()
    r._radio._ctrl_transport._udp_transport = MagicMock()
    r._radio._civ_transport = MagicMock()
    r._radio._conn_state = RadioConnectionState.CONNECTED
    r._radio.get_swr = AsyncMock(return_value=1.7)

    try:
        result = r.get_swr()
        assert result == 1.7
        assert isinstance(result, float)
        r._radio.get_swr.assert_awaited_once()
    finally:
        r._loop.close()
