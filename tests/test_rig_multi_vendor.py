"""Tests for multi-vendor rig profile support.

Tests for new optional sections: [protocol], [controls], [meters], [[rules]].
Also tests loading of new vendor TOML files (ftx1, x6100, tx500).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles import RadioProfile
from rigplane.rig_loader import (
    VALID_CONTROL_STYLES,
    VALID_PROTOCOL_TYPES,
    VALID_VFO_SCHEMES,
    RigConfig,
    RigLoadError,
    discover_rigs,
    load_rig,
)

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"


def _write_toml(tmp_path: Path, content: str, name: str = "test.toml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return p


_BASE_TOML = """\
[radio]
id = "test_radio"
model = "TestRadio"
receiver_count = 1
has_lan = false
has_wifi = false

[capabilities]
features = ["audio", "tx", "meters"]

[modes]
list = ["USB", "LSB", "CW"]

[filters]
list = ["FIL1", "FIL2"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 100000
end_hz = 60000000
"""

_CIV_BASE_TOML = """\
[radio]
id = "test_civ"
model = "TestCIV"
civ_addr = 0x70
receiver_count = 1
has_lan = false
has_wifi = false

[capabilities]
features = ["audio", "tx", "meters"]

[modes]
list = ["USB", "LSB", "CW"]

[filters]
list = ["FIL1", "FIL2"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 100000
end_hz = 60000000
"""


class TestProtocolTypes:
    """Protocol type parsing and validation."""

    def test_default_protocol_civ(self, tmp_path):
        """No [protocol] section → defaults to 'civ'."""
        p = _write_toml(tmp_path, _CIV_BASE_TOML)
        rig = load_rig(p)
        assert rig.protocol_type == "civ"

    def test_explicit_civ_protocol(self, tmp_path):
        p = _write_toml(tmp_path, _CIV_BASE_TOML + '\n[protocol]\ntype = "civ"\n')
        rig = load_rig(p)
        assert rig.protocol_type == "civ"

    def test_kenwood_cat_protocol(self, tmp_path):
        p = _write_toml(tmp_path, _BASE_TOML + '\n[protocol]\ntype = "kenwood_cat"\n')
        rig = load_rig(p)
        assert rig.protocol_type == "kenwood_cat"

    def test_yaesu_cat_protocol(self, tmp_path):
        p = _write_toml(tmp_path, _BASE_TOML + '\n[protocol]\ntype = "yaesu_cat"\n')
        rig = load_rig(p)
        assert rig.protocol_type == "yaesu_cat"

    def test_invalid_protocol_type(self, tmp_path):
        p = _write_toml(tmp_path, _BASE_TOML + '\n[protocol]\ntype = "icom_special"\n')
        with pytest.raises(RigLoadError, match="protocol"):
            load_rig(p)

    def test_valid_protocol_types_constant(self):
        assert "civ" in VALID_PROTOCOL_TYPES
        assert "kenwood_cat" in VALID_PROTOCOL_TYPES
        assert "yaesu_cat" in VALID_PROTOCOL_TYPES


class TestControlStyles:
    """Control style parsing."""

    def test_no_controls_section(self, tmp_path):
        """Backward compat: no [controls] → controls is None."""
        p = _write_toml(tmp_path, _CIV_BASE_TOML)
        rig = load_rig(p)
        assert rig.controls is None

    def test_attenuator_stepped(self, tmp_path):
        toml = _CIV_BASE_TOML + '\n[controls.attenuator]\nstyle = "stepped"\n'
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.controls is not None
        assert rig.controls["attenuator"]["style"] == "stepped"

    def test_attenuator_toggle(self, tmp_path):
        toml = _CIV_BASE_TOML + '\n[controls.attenuator]\nstyle = "toggle"\n'
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.controls["attenuator"]["style"] == "toggle"

    def test_nb_level_is_toggle(self, tmp_path):
        toml = _CIV_BASE_TOML + '\n[controls.nb]\nstyle = "level_is_toggle"\n'
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.controls["nb"]["style"] == "level_is_toggle"

    def test_invalid_control_style(self, tmp_path):
        toml = _CIV_BASE_TOML + '\n[controls.attenuator]\nstyle = "unknown_style"\n'
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="style"):
            load_rig(p)

    def test_valid_control_styles_constant(self):
        assert "toggle" in VALID_CONTROL_STYLES
        assert "stepped" in VALID_CONTROL_STYLES
        assert "selector" in VALID_CONTROL_STYLES
        assert "toggle_and_level" in VALID_CONTROL_STYLES
        assert "level_is_toggle" in VALID_CONTROL_STYLES


class TestMeterCalibration:
    """Meter calibration parsing."""

    def test_no_meters_section(self, tmp_path):
        """Backward compat: no [meters] → calibrations is None."""
        p = _write_toml(tmp_path, _CIV_BASE_TOML)
        rig = load_rig(p)
        assert rig.meter_calibrations is None
        assert rig.meter_redlines is None

    def test_s_meter_calibration_points(self, tmp_path):
        toml = (
            _CIV_BASE_TOML
            + "\n[meters.s_meter]\nredline_raw = 130\n"
            + '\n[[meters.s_meter.calibration]]\nraw = 0\nactual = -54.0\nlabel = "S0"\n'
            + '\n[[meters.s_meter.calibration]]\nraw = 130\nactual = 0.0\nlabel = "S9"\n'
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.meter_calibrations is not None
        cals = rig.meter_calibrations["s_meter"]
        assert len(cals) == 2
        assert cals[0]["raw"] == 0
        assert cals[0]["actual"] == -54.0
        assert cals[0]["label"] == "S0"
        assert cals[1]["raw"] == 130
        assert cals[1]["label"] == "S9"

    def test_s_meter_redline(self, tmp_path):
        toml = (
            _CIV_BASE_TOML
            + "\n[meters.s_meter]\nredline_raw = 130\n"
            + '\n[[meters.s_meter.calibration]]\nraw = 0\nactual = -54.0\nlabel = "S0"\n'
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.meter_redlines is not None
        assert rig.meter_redlines["s_meter"] == 130


class TestConstraintRules:
    """Constraint rules parsing."""

    def test_no_rules(self, tmp_path):
        """Backward compat: no [[rules]] → empty tuple."""
        p = _write_toml(tmp_path, _CIV_BASE_TOML)
        rig = load_rig(p)
        assert rig.rules == ()

    def test_mutex_rule(self, tmp_path):
        toml = (
            _CIV_BASE_TOML
            + '\n[[rules]]\nkind = "mutex"\nfields = ["attenuator", "preamp"]\n'
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert len(rig.rules) == 1
        assert rig.rules[0]["kind"] == "mutex"
        assert rig.rules[0]["fields"] == ["attenuator", "preamp"]

    def test_disables_rule(self, tmp_path):
        toml = (
            _CIV_BASE_TOML
            + '\n[[rules]]\nkind = "disables"\nwhen_active = "digisel"\ndisables = ["preamp"]\nreason = "test"\n'
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.rules[0]["kind"] == "disables"
        assert rig.rules[0]["when_active"] == "digisel"

    def test_requires_rule(self, tmp_path):
        toml = (
            _CIV_BASE_TOML
            + '\n[[rules]]\nkind = "requires"\nfield = "split"\nrequires = "tx"\n'
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.rules[0]["kind"] == "requires"

    def test_invalid_rule_kind(self, tmp_path):
        toml = _CIV_BASE_TOML + '\n[[rules]]\nkind = "invalid_kind"\n'
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="kind"):
            load_rig(p)


class TestVfoSchemes:
    """Extended VFO schemes."""

    def test_ab_shared_scheme(self, tmp_path):
        toml = _BASE_TOML.replace('scheme = "ab"', 'scheme = "ab_shared"')
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.vfo_scheme == "ab_shared"

    def test_single_scheme(self, tmp_path):
        toml = _BASE_TOML.replace('scheme = "ab"', 'scheme = "single"')
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.vfo_scheme == "single"

    def test_valid_vfo_schemes_constant(self):
        assert "ab" in VALID_VFO_SCHEMES
        assert "main_sub" in VALID_VFO_SCHEMES
        assert "ab_shared" in VALID_VFO_SCHEMES
        assert "single" in VALID_VFO_SCHEMES


class TestMultiVendorProfiles:
    """Load actual TOML profiles for all vendors."""

    def test_load_ftx1(self):
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert isinstance(rig, RigConfig)
        assert rig.id == "yaesu_ftx1"
        assert rig.model == "FTX-1"

    def test_load_x6100(self):
        rig = load_rig(RIGS_DIR / "x6100.toml")
        assert isinstance(rig, RigConfig)
        assert rig.id == "xiegu_x6100"
        assert rig.model == "X6100"

    def test_load_tx500(self):
        rig = load_rig(RIGS_DIR / "tx500.toml")
        assert isinstance(rig, RigConfig)
        assert rig.id == "lab599_tx500"
        assert rig.model == "TX-500"

    def test_discover_finds_all_five(self):
        rigs = discover_rigs(RIGS_DIR)
        models = set(rigs.keys())
        assert "IC-7610" in models
        assert "IC-7300" in models
        assert "FTX-1" in models
        assert "X6100" in models
        assert "TX-500" in models

    def test_ftx1_protocol_yaesu_cat(self):
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert rig.protocol_type == "yaesu_cat"

    def test_x6100_protocol_civ(self):
        rig = load_rig(RIGS_DIR / "x6100.toml")
        assert rig.protocol_type == "civ"
        assert rig.civ_addr == 0x70

    def test_x6100_filter_width_capability_disabled(self):
        """Regression guard for issue #1159.

        After PR #1157 unified ``set_filter_width`` on a per-mode segmented
        BCD-index path, the X6100 profile (which has no segment tables and
        no wfview support) must NOT advertise the ``filter_width`` capability
        — otherwise every call would raise ``CommandError`` in main HEAD.

        The IC-* rigs (which ship segment tables) must keep the capability.
        """
        x6100 = load_rig(RIGS_DIR / "x6100.toml").to_profile()
        assert "filter_width" not in x6100.capabilities, (
            "X6100 must NOT advertise filter_width until segment tables are "
            "verified against hardware (issue #1159)."
        )
        # Sanity: confirmed CI-V rigs still expose filter_width.
        for model_toml in ("ic7300.toml", "ic705.toml", "ic7610.toml", "ic9700.toml"):
            profile = load_rig(RIGS_DIR / model_toml).to_profile()
            assert "filter_width" in profile.capabilities, (
                f"{model_toml} regression: filter_width must remain advertised."
            )

    def test_tx500_protocol_kenwood_cat(self):
        rig = load_rig(RIGS_DIR / "tx500.toml")
        assert rig.protocol_type == "kenwood_cat"

    def test_ftx1_17_modes(self):
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert len(rig.modes) == 17

    def test_ftx1_meter_calibration(self):
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert rig.meter_calibrations is not None
        assert "s_meter" in rig.meter_calibrations
        assert len(rig.meter_calibrations["s_meter"]) >= 6

    def test_ftx1_nb_level_is_toggle(self):
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert rig.controls is not None
        assert rig.controls["nb"]["style"] == "level_is_toggle"

    def test_ftx1_profile_exposes_power_max_watts(self):
        profile = load_rig(RIGS_DIR / "ftx1.toml").to_profile()
        assert profile.max_watts == 100

    def test_ic7610_mutex_rule(self):
        rig = load_rig(RIGS_DIR / "ic7610.toml")
        mutex_rules = [r for r in rig.rules if r["kind"] == "mutex"]
        assert len(mutex_rules) >= 1
        mutex = mutex_rules[0]
        assert "attenuator" in mutex["fields"]
        assert "preamp" in mutex["fields"]

    def test_ic7610_disables_rule(self):
        rig = load_rig(RIGS_DIR / "ic7610.toml")
        disables_rules = [r for r in rig.rules if r["kind"] == "disables"]
        assert len(disables_rules) >= 1

    def test_tx500_commands_are_wired(self):
        """MOR-684: kenwood_cat CAT command strings are wired in [commands].

        Previously [commands] was an empty stub; the loaded profile now carries
        the rev.2 CAT command set as ``CatCommandSpec`` entries.
        """
        rig = load_rig(RIGS_DIR / "tx500.toml")
        assert isinstance(rig.commands, dict)
        assert rig.commands  # no longer empty
        for spec in rig.commands.values():
            assert isinstance(spec, CatCommandSpec)

    def test_tx500_mode_map_matches_rev2(self):
        """MOR-684: mode list matches Lab599 CAT Protocol rev.2 exactly.

        rev.2 MD register map: 1=LSB 2=USB 3=CW 4=FM 5=AM 6=DIG 7=CW-R.
        No FSK / RTTY / register 8 / register 9 exist for the TX-500.
        """
        rig = load_rig(RIGS_DIR / "tx500.toml")
        modes = set(rig.modes)
        assert "DIG" in modes  # rev.2 label (was "DIGI")
        assert "CW-R" in modes
        # FSK / RTTY are NOT TX-500 modes per rev.2.
        assert "RTTY" not in modes
        assert "RTTY-R" not in modes
        assert "FSK" not in modes
        assert "FSK-R" not in modes
        # The seven documented modes, nothing else.
        assert modes == {"LSB", "USB", "CW", "FM", "AM", "DIG", "CW-R"}

    def test_tx500_set_mode_register_comment_documents_rev2(self):
        """MOR-684: set_mode write template encodes the single mode register."""
        rig = load_rig(RIGS_DIR / "tx500.toml")
        set_mode = rig.commands["set_mode"]
        assert isinstance(set_mode, CatCommandSpec)
        assert set_mode.write == "MD{mode};"

    def test_tx500_power_is_cat_settable(self):
        """MOR-684: power is CAT-controllable via PC (010-100) per rev.2.

        The audit's gap D flagged the old "NOT controllable via CAT" note as
        wrong; rev.2 documents the PC output-power command.
        """
        rig = load_rig(RIGS_DIR / "tx500.toml")
        assert "power_control" in rig.capabilities
        set_power = rig.commands["set_power"]
        assert isinstance(set_power, CatCommandSpec)
        assert set_power.write == "PC{power:03d};"
        assert rig.commands["get_power"].read == "PC;"

    def test_backward_compat_existing_tests_still_pass(self):
        """IC-7610 and IC-7300 load without errors."""
        ic7610 = load_rig(RIGS_DIR / "ic7610.toml")
        ic7300 = load_rig(RIGS_DIR / "ic7300.toml")
        assert ic7610.civ_addr == 0x98
        assert ic7300.civ_addr == 0x94

    def test_tx500_profile_without_power_section_still_loads(self):
        """Backward compat: profiles without ``[power].max_watts`` still load."""
        profile = load_rig(RIGS_DIR / "tx500.toml").to_profile()
        assert profile.max_watts is None


class TestProfileBuilding:
    """to_profile() with new fields."""

    def test_protocol_type_in_profile(self, tmp_path):
        p = _write_toml(tmp_path, _BASE_TOML + '\n[protocol]\ntype = "kenwood_cat"\n')
        rig = load_rig(p)
        profile = rig.to_profile()
        assert isinstance(profile, RadioProfile)
        assert profile.protocol_type == "kenwood_cat"

    def test_controls_in_profile(self, tmp_path):
        toml = _CIV_BASE_TOML + '\n[controls.attenuator]\nstyle = "stepped"\n'
        p = _write_toml(tmp_path, toml)
        profile = load_rig(p).to_profile()
        assert profile.controls is not None
        assert profile.controls["attenuator"]["style"] == "stepped"

    def test_rules_in_profile(self, tmp_path):
        toml = (
            _CIV_BASE_TOML
            + '\n[[rules]]\nkind = "mutex"\nfields = ["attenuator", "preamp"]\n'
        )
        p = _write_toml(tmp_path, toml)
        profile = load_rig(p).to_profile()
        assert len(profile.rules) == 1
        assert profile.rules[0]["kind"] == "mutex"

    def test_meter_calibrations_in_profile(self, tmp_path):
        toml = (
            _CIV_BASE_TOML
            + "\n[meters.s_meter]\nredline_raw = 130\n"
            + '\n[[meters.s_meter.calibration]]\nraw = 0\nactual = -54.0\nlabel = "S0"\n'
        )
        p = _write_toml(tmp_path, toml)
        profile = load_rig(p).to_profile()
        assert profile.meter_calibrations is not None
        assert "s_meter" in profile.meter_calibrations

    def test_default_protocol_type_in_profile(self, tmp_path):
        p = _write_toml(tmp_path, _CIV_BASE_TOML)
        profile = load_rig(p).to_profile()
        assert profile.protocol_type == "civ"
