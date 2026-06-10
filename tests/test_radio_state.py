"""Tests for RadioState and ReceiverState dataclasses."""

from __future__ import annotations

from rigplane.radio_state import (
    RadioState,
    ReceiverState,
    VfoSlotState,
    YaesuStateExtension,
)

# ---------------------------------------------------------------------------
# ReceiverState defaults
# ---------------------------------------------------------------------------


def test_receiver_state_defaults() -> None:
    rx = ReceiverState()
    assert rx.freq == 0
    assert rx.mode == "USB"
    assert rx.filter is None
    assert rx.filter_width is None
    assert rx.data_mode == 0
    assert rx.att == 0
    assert rx.preamp == 0
    assert rx.nb is False
    assert rx.nr is False
    assert rx.digisel is False
    assert rx.ipplus is False
    assert rx.s_meter_sql_open is False
    assert rx.agc == 0
    assert rx.audio_peak_filter == 0
    assert rx.auto_notch is False
    assert rx.manual_notch is False
    assert rx.twin_peak_filter is False
    assert rx.filter_shape == 0
    assert rx.agc_time_constant == 0
    assert rx.af_level == 0
    assert rx.rf_gain == 0
    assert rx.squelch == 0
    assert rx.s_meter == 0
    assert rx.apf_type_level == 0
    assert rx.nr_level == 0
    assert rx.pbt_inner == 128
    assert rx.pbt_outer == 128
    assert rx.nb_level == 0
    assert rx.digisel_shift == 0
    assert rx.af_mute is False


def test_receiver_state_field_update() -> None:
    rx = ReceiverState()
    rx.freq = 14_074_000
    rx.mode = "USB"
    rx.filter = 2
    rx.att = 18
    rx.preamp = 1
    rx.nb = True
    rx.s_meter = 120
    assert rx.freq == 14_074_000
    assert rx.filter == 2
    assert rx.att == 18
    assert rx.preamp == 1
    assert rx.nb is True
    assert rx.s_meter == 120


# ---------------------------------------------------------------------------
# RadioState defaults
# ---------------------------------------------------------------------------


def test_radio_state_defaults() -> None:
    rs = RadioState()
    assert rs.active == "MAIN"
    assert rs.ptt is False
    assert rs.power_level == 0
    assert rs.split is False
    assert rs.dual_watch is False
    assert rs.overflow is False
    assert rs.power_meter == 0
    assert rs.swr_meter == 0
    assert rs.alc_meter == 0
    assert rs.cw_pitch == 0
    assert rs.mic_gain == 0
    assert rs.key_speed == 0
    assert rs.notch_filter == 0
    assert rs.compressor_on is False
    assert rs.compressor_level == 0
    assert rs.monitor_on is False
    assert rs.break_in_delay == 0
    assert rs.break_in == 0
    assert rs.dial_lock is False
    assert rs.drive_gain == 0
    assert rs.monitor_gain == 0
    assert rs.vox_on is False
    assert rs.vox_gain == 0
    assert rs.anti_vox_gain == 0
    assert rs.ssb_tx_bandwidth == 0
    assert rs.ref_adjust == 0
    assert rs.dash_ratio == 0
    assert rs.nb_depth == 0
    assert rs.nb_width == 0
    assert rs.scope_controls.receiver == 0
    assert rs.scope_controls.dual is False
    assert rs.scope_controls.during_tx is False
    assert rs.scope_controls.fixed_edge.start_hz == 0
    assert isinstance(rs.main, ReceiverState)
    assert isinstance(rs.sub, ReceiverState)


def test_radio_state_main_sub_are_independent() -> None:
    rs = RadioState()
    rs.main.freq = 14_074_000
    rs.sub.freq = 7_000_000
    assert rs.main.freq == 14_074_000
    assert rs.sub.freq == 7_000_000


# ---------------------------------------------------------------------------
# receiver() method
# ---------------------------------------------------------------------------


def test_receiver_main_returns_main() -> None:
    rs = RadioState()
    rs.main.freq = 14_000_000
    assert rs.receiver("MAIN") is rs.main
    assert rs.receiver("MAIN").freq == 14_000_000


def test_receiver_sub_returns_sub() -> None:
    rs = RadioState()
    rs.sub.freq = 7_000_000
    assert rs.receiver("SUB") is rs.sub
    assert rs.receiver("SUB").freq == 7_000_000


def test_receiver_unknown_falls_back_to_sub() -> None:
    # Any non-"MAIN" string returns sub (matches the ternary logic)
    rs = RadioState()
    assert rs.receiver("OTHER") is rs.sub


# ---------------------------------------------------------------------------
# to_dict()
# ---------------------------------------------------------------------------


def test_to_dict_structure() -> None:
    rs = RadioState()
    d = rs.to_dict()
    assert set(d.keys()) == {
        "active",
        "power_on",
        "ptt",
        "power_level",
        "split",
        "dual_watch",
        "scanning",
        "scan_type",
        "scan_resume_mode",
        "tuning_step",
        "overflow",
        "tuner_status",
        "tx_freq_monitor",
        "rit_freq",
        "rit_on",
        "rit_tx",
        "comp_meter",
        "vd_meter",
        "id_meter",
        "power_meter",
        "swr_meter",
        "alc_meter",
        "cw_pitch",
        "mic_gain",
        "key_speed",
        "notch_filter",
        "main_sub_tracking",
        "compressor_on",
        "compressor_level",
        "monitor_on",
        "break_in_delay",
        "break_in",
        "cw_spot",
        "yaesu",
        "dial_lock",
        "drive_gain",
        "monitor_gain",
        "vfo_select",
        "vox_on",
        "vox_gain",
        "anti_vox_gain",
        "vox_delay",
        "ssb_tx_bandwidth",
        "ref_adjust",
        "dash_ratio",
        "nb_depth",
        "nb_width",
        "tx_antenna",
        "rx_antenna_1",
        "rx_antenna_2",
        "data_off_mod_input",
        "data1_mod_input",
        "data2_mod_input",
        "data3_mod_input",
        "tx_band_edges",
        "scope_controls",
        "main",
        "sub",
    }
    assert d["active"] == "MAIN"
    assert d["ptt"] is False
    assert d["power_level"] == 0
    assert d["split"] is False
    assert d["dual_watch"] is False


def test_to_dict_main_keys() -> None:
    rs = RadioState()
    main = rs.to_dict()["main"]
    expected_keys = {
        "freq",
        "mode",
        "filter",
        "filter_width",
        "data_mode",
        "att",
        "preamp",
        "nb",
        "nr",
        "digisel",
        "ipplus",
        "s_meter_sql_open",
        "agc",
        "audio_peak_filter",
        "auto_notch",
        "manual_notch",
        "twin_peak_filter",
        "filter_shape",
        "agc_time_constant",
        "af_level",
        "rf_gain",
        "squelch",
        "s_meter",
        "apf_type_level",
        "nr_level",
        "pbt_inner",
        "pbt_outer",
        "nb_level",
        "digisel_shift",
        "af_mute",
        "contour",
        "apf_on",
        "apf_freq",
        "if_shift",
        "repeater_tone",
        "repeater_tsql",
        "tone_freq",
        "tsql_freq",
        "manual_notch_freq",
        "manual_notch_width",
        "narrow",
        "vfo_a",
        "vfo_b",
        "active_slot",
    }
    assert set(main.keys()) == expected_keys


def test_to_dict_reflects_field_changes() -> None:
    rs = RadioState()
    rs.main.freq = 14_074_000
    rs.main.mode = "USB"
    rs.main.att = 18
    rs.ptt = True
    rs.split = True
    rs.scope_controls.receiver = 1
    rs.scope_controls.during_tx = True
    d = rs.to_dict()
    assert d["ptt"] is True
    assert d["split"] is True
    assert d["main"]["freq"] == 14_074_000
    assert d["main"]["att"] == 18
    assert d["scope_controls"]["receiver"] == 1
    assert d["scope_controls"]["during_tx"] is True


def test_to_dict_scope_controls_structure() -> None:
    rs = RadioState()
    scope = rs.to_dict()["scope_controls"]
    assert set(scope.keys()) == {
        "receiver",
        "dual",
        "mode",
        "span",
        "edge",
        "hold",
        "ref_db",
        "speed",
        "during_tx",
        "center_type",
        "vbw_narrow",
        "rbw",
        "fixed_edge",
    }
    assert set(scope["fixed_edge"].keys()) == {
        "range_index",
        "edge",
        "start_hz",
        "end_hz",
    }


def test_to_dict_sub_independent() -> None:
    rs = RadioState()
    rs.main.freq = 14_000_000
    rs.sub.freq = 7_000_000
    d = rs.to_dict()
    assert d["main"]["freq"] == 14_000_000
    assert d["sub"]["freq"] == 7_000_000


def test_to_dict_is_json_serialisable() -> None:
    import json

    rs = RadioState()
    rs.main.freq = 14_074_000
    rs.main.nb = True
    rs.ptt = False
    payload = json.dumps(rs.to_dict())
    reloaded = json.loads(payload)
    assert reloaded["main"]["freq"] == 14_074_000
    assert reloaded["main"]["nb"] is True


# --- Transceiver status family (#136) ---


class TestTransceiverStatusState:
    """Test RadioState fields for transceiver_status family."""

    def test_tuner_status_default(self) -> None:
        rs = RadioState()
        assert rs.tuner_status == 0

    def test_tuner_status_set(self) -> None:
        rs = RadioState()
        rs.tuner_status = 2
        assert rs.tuner_status == 2
        d = rs.to_dict()
        assert d["tuner_status"] == 2

    def test_tx_freq_monitor_default(self) -> None:
        rs = RadioState()
        assert rs.tx_freq_monitor is False

    def test_rit_fields_defaults(self) -> None:
        rs = RadioState()
        assert rs.rit_freq == 0
        assert rs.rit_on is False
        assert rs.rit_tx is False

    def test_rit_fields_set(self) -> None:
        rs = RadioState()
        rs.rit_freq = -150
        rs.rit_on = True
        rs.rit_tx = True
        d = rs.to_dict()
        assert d["rit_freq"] == -150
        assert d["rit_on"] is True
        assert d["rit_tx"] is True

    def test_meter_fields_defaults(self) -> None:
        rs = RadioState()
        assert rs.comp_meter == 0
        assert rs.vd_meter == 0
        assert rs.id_meter == 0

    def test_meter_fields_in_dict(self) -> None:
        rs = RadioState()
        rs.comp_meter = 42
        rs.vd_meter = 130
        rs.id_meter = 55
        d = rs.to_dict()
        assert d["comp_meter"] == 42
        assert d["vd_meter"] == 130
        assert d["id_meter"] == 55


# --- VfoSlotState + per-receiver active_slot (#709) ------------------------


class TestVfoSlotState:
    """ReceiverState exposes per-VFO slot state with legacy-field fallback."""

    def test_slot_defaults(self) -> None:
        slot = VfoSlotState()
        assert slot.freq_hz == 0
        assert slot.mode == "USB"
        assert slot.filter_num is None
        assert slot.data_mode == 0

    def test_legacy_freq_kwarg_populates_vfo_a(self) -> None:
        rx = ReceiverState(freq=7_074_000)
        assert rx.vfo_a.freq_hz == 7_074_000
        assert rx.vfo_b.freq_hz == 0
        assert rx.freq == 7_074_000  # property reads vfo_a (active by default)

    def test_legacy_mode_filter_kwargs_populate_vfo_a(self) -> None:
        rx = ReceiverState(freq=14_074_000, mode="CW", filter=2, data_mode=1)
        assert rx.vfo_a.freq_hz == 14_074_000
        assert rx.vfo_a.mode == "CW"
        assert rx.vfo_a.filter_num == 2
        assert rx.vfo_a.data_mode == 1
        assert rx.mode == "CW"
        assert rx.filter == 2
        assert rx.data_mode == 1

    def test_active_slot_b_reflects_vfo_b(self) -> None:
        rx = ReceiverState(freq=14_074_000)
        rx.vfo_b = VfoSlotState(freq_hz=7_000_000, mode="LSB")
        rx.active_slot = "B"
        assert rx.freq == 7_000_000
        assert rx.mode == "LSB"
        # Writing via property mutates vfo_b (the active slot), not vfo_a.
        rx.freq = 3_573_000
        assert rx.vfo_b.freq_hz == 3_573_000
        assert rx.vfo_a.freq_hz == 14_074_000

    def test_to_dict_from_dict_round_trip_preserves_both_slots(self) -> None:
        rs = RadioState()
        rs.main.vfo_a = VfoSlotState(freq_hz=14_074_000, mode="USB", filter_num=2)
        rs.main.vfo_b = VfoSlotState(freq_hz=7_074_000, mode="CW", filter_num=1)
        rs.main.active_slot = "B"
        d = rs.to_dict()
        assert d["main"]["vfo_a"]["freq_hz"] == 14_074_000
        assert d["main"]["vfo_b"]["freq_hz"] == 7_074_000
        assert d["main"]["active_slot"] == "B"
        assert d["main"]["freq"] == 7_074_000  # legacy view = vfo_b

        restored = RadioState._receiver_from_dict(d["main"])
        assert restored.vfo_a.freq_hz == 14_074_000
        assert restored.vfo_a.mode == "USB"
        assert restored.vfo_a.filter_num == 2
        assert restored.vfo_b.freq_hz == 7_074_000
        assert restored.vfo_b.mode == "CW"
        assert restored.vfo_b.filter_num == 1
        assert restored.active_slot == "B"
        assert restored.freq == 7_074_000

    def test_from_dict_legacy_fallback_populates_vfo_a(self) -> None:
        """A legacy dict without slot keys falls back to vfo_a via top-level freq/mode."""
        legacy = {"freq": 7_074_000, "mode": "CW", "filter": 3, "data_mode": 0}
        rx = RadioState._receiver_from_dict(legacy)
        assert rx.vfo_a.freq_hz == 7_074_000
        assert rx.vfo_a.mode == "CW"
        assert rx.vfo_a.filter_num == 3
        assert rx.active_slot == "A"


# ---------------------------------------------------------------------------
# State-contract sweep (#1169): cw_spot tri-state + Yaesu extension
# ---------------------------------------------------------------------------


class TestCwSpotTriState:
    """``cw_spot`` is ``bool | None`` — None means "not populated"."""

    def test_default_is_none(self) -> None:
        """Icom backends never assign cw_spot — default must be None."""
        rs = RadioState()
        assert rs.cw_spot is None

    def test_to_dict_serialises_none(self) -> None:
        rs = RadioState()
        d = rs.to_dict()
        assert d["cw_spot"] is None

    def test_to_dict_serialises_true(self) -> None:
        rs = RadioState()
        rs.cw_spot = True
        assert rs.to_dict()["cw_spot"] is True

    def test_to_dict_serialises_false(self) -> None:
        rs = RadioState()
        rs.cw_spot = False
        assert rs.to_dict()["cw_spot"] is False


class TestYaesuStateExtension:
    """Yaesu-only flags live in ``state.yaesu`` namespace."""

    def test_default_yaesu_is_none(self) -> None:
        """Generic RadioState (Icom path) leaves yaesu unset."""
        rs = RadioState()
        assert rs.yaesu is None

    def test_yaesu_extension_defaults(self) -> None:
        ext = YaesuStateExtension()
        assert ext.rx_func_mode is None
        assert ext.tx_func_mode is None

    def test_yaesu_assigned_namespace(self) -> None:
        rs = RadioState()
        rs.yaesu = YaesuStateExtension(rx_func_mode=1, tx_func_mode=0)
        assert rs.yaesu is not None
        assert rs.yaesu.rx_func_mode == 1
        assert rs.yaesu.tx_func_mode == 0

    def test_to_dict_yaesu_none(self) -> None:
        rs = RadioState()
        assert rs.to_dict()["yaesu"] is None

    def test_to_dict_yaesu_populated(self) -> None:
        rs = RadioState()
        rs.yaesu = YaesuStateExtension(rx_func_mode=1, tx_func_mode=1)
        d = rs.to_dict()
        assert d["yaesu"] == {"rx_func_mode": 1, "tx_func_mode": 1}


class TestModInputState:
    """MOR-615: per-DATA-group MOD-input source fields (IC-7610 0x1A 05 00 91-94).

    Values use the rig enum 0=MIC, 1=ACC, 2=MIC+ACC, 3=USB, 4=MIC+USB, 5=LAN;
    ``None`` means "not yet read from the radio".
    """

    def test_defaults_are_none(self) -> None:
        rs = RadioState()
        assert rs.data_off_mod_input is None
        assert rs.data1_mod_input is None
        assert rs.data2_mod_input is None
        assert rs.data3_mod_input is None

    def test_to_dict_serialises_unknown_as_none(self) -> None:
        d = RadioState().to_dict()
        assert d["data_off_mod_input"] is None
        assert d["data1_mod_input"] is None
        assert d["data2_mod_input"] is None
        assert d["data3_mod_input"] is None

    def test_to_dict_serialises_assigned_sources(self) -> None:
        rs = RadioState()
        rs.data_off_mod_input = 0  # MIC
        rs.data1_mod_input = 3  # USB
        rs.data2_mod_input = 1  # ACC
        rs.data3_mod_input = 5  # LAN
        d = rs.to_dict()
        assert d["data_off_mod_input"] == 0
        assert d["data1_mod_input"] == 3
        assert d["data2_mod_input"] == 1
        assert d["data3_mod_input"] == 5
