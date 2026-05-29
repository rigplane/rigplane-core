"""Tests for rig_loader and command_map modules.

TDD: these tests were written FIRST, then the implementation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rigplane.command_map import CommandMap
from rigplane.profiles import BandInfo, FreqRangeInfo, RadioProfile, get_radio_profile
from rigplane.rig_loader import RigConfig, RigLoadError, discover_rigs, load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
TEMPLATE_PATH = RIGS_DIR / "ic7610.toml"


# ── Helpers ──────────────────────────────────────────────────────


def _write_toml(tmp_path: Path, content: str, name: str = "test.toml") -> Path:
    """Write a TOML string to a temp file and return the path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return p


_MINIMAL_TOML = """\
[radio]
id = "icom_ic7300"
model = "IC-7300"
civ_addr = 0x94
receiver_count = 1
has_lan = true
has_wifi = false

[spectrum]
seq_max = 11
amp_max = 160
data_len_max = 475

[capabilities]
features = ["audio", "scope", "meters", "tx"]

[modes]
list = ["USB", "LSB", "CW"]

[filters]
list = ["FIL1", "FIL2"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 30000
end_hz = 60000000

[commands]
get_freq = [0x03]
set_freq = [0x05]

[commands.overrides]
"""


# ── RigConfig loading ────────────────────────────────────────────


class TestLoadRig:
    """load_rig() parsing and validation."""

    def test_load_template(self):
        rig = load_rig(TEMPLATE_PATH)
        assert isinstance(rig, RigConfig)
        assert rig.model == "IC-7610"
        assert rig.id == "icom_ic7610"
        assert rig.civ_addr == 0x98
        assert rig.receiver_count == 2

    def test_load_minimal(self, tmp_path):
        p = _write_toml(tmp_path, _MINIMAL_TOML)
        rig = load_rig(p)
        assert rig.model == "IC-7300"

    def test_missing_radio_section(self, tmp_path):
        p = _write_toml(
            tmp_path,
            """\
            [spectrum]
            seq_max = 1
            amp_max = 1
            data_len_max = 1
        """,
        )
        with pytest.raises(RigLoadError, match="radio"):
            load_rig(p)

    def test_missing_required_field(self, tmp_path):
        toml = _MINIMAL_TOML.replace('id = "icom_ic7300"\n', "")
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="id"):
            load_rig(p)

    def test_civ_addr_out_of_range(self, tmp_path):
        toml = _MINIMAL_TOML.replace("civ_addr = 0x94", "civ_addr = 256")
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="civ_addr"):
            load_rig(p)

    def test_empty_capabilities(self, tmp_path):
        toml = _MINIMAL_TOML.replace(
            'features = ["audio", "scope", "meters", "tx"]',
            "features = []",
        )
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="capabilities"):
            load_rig(p)

    def test_unknown_capability(self, tmp_path):
        toml = _MINIMAL_TOML.replace(
            'features = ["audio", "scope", "meters", "tx"]',
            'features = ["audio", "teleportation"]',
        )
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="teleportation"):
            load_rig(p)

    def test_invalid_vfo_scheme(self, tmp_path):
        toml = _MINIMAL_TOML.replace('scheme = "ab"', 'scheme = "xyz"')
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="vfo.*scheme"):
            load_rig(p)

    def test_file_not_found(self, tmp_path):
        with pytest.raises(RigLoadError, match="not found"):
            load_rig(tmp_path / "nonexistent.toml")

    def test_invalid_toml_syntax(self, tmp_path):
        p = _write_toml(tmp_path, "this is not [valid toml")
        with pytest.raises(RigLoadError):
            load_rig(p)

    def test_merges_ui_keyboard_overrides_with_default_profile(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[ui.keyboard]
help_title = "Custom Keyboard"
leader_timeout_ms = 900
alt_hints = true

[[ui.keyboard.bindings]]
id = "tune-up"
section = "Tuning"
label = "Tune up"
key = "ArrowUp"
action = "tune"
repeatable = true
[ui.keyboard.bindings.params]
direction = "up"
fine = false
""",
        )

        rig = load_rig(p)

        assert rig.keyboard is not None
        assert rig.keyboard.help_title == "Custom Keyboard"
        assert rig.keyboard.leader_timeout_ms == 900
        assert rig.keyboard.alt_hints is True
        # Without _keyboard-default.toml in tmp_path, only the override binding is present
        binding = next(
            binding for binding in rig.keyboard.bindings if binding.id == "tune-up"
        )
        assert binding.id == "tune-up"
        assert binding.sequence == ("ArrowUp",)
        assert binding.action == "tune"
        assert binding.params == {"direction": "up", "fine": False}

    def test_loads_default_keyboard_profile_without_ui_section(self, tmp_path):
        rig = load_rig(_write_toml(tmp_path, _MINIMAL_TOML))

        # Without _keyboard-default.toml in tmp_path, keyboard is None
        assert rig.keyboard is None

    def test_loads_default_keyboard_profile_with_file(self, tmp_path):
        import shutil

        default_kb = (
            Path(__file__).resolve().parent.parent / "rigs" / "_keyboard-default.toml"
        )
        if default_kb.exists():
            shutil.copy(default_kb, tmp_path / "_keyboard-default.toml")
            rig = load_rig(_write_toml(tmp_path, _MINIMAL_TOML))
            assert rig.keyboard is not None
            assert rig.keyboard.help_title == "Radio Keyboard"
            assert any(
                binding.action == "toggle_help" for binding in rig.keyboard.bindings
            )


# ── RadioProfile building ───────────────────────────────────────


class TestToProfile:
    """RigConfig.to_profile() produces correct RadioProfile."""

    def test_returns_radio_profile(self):
        rig = load_rig(TEMPLATE_PATH)
        profile = rig.to_profile()
        assert isinstance(profile, RadioProfile)

    def test_civ_addr(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.civ_addr == 0x98

    def test_receiver_count(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.receiver_count == 2

    def test_transceiver_count_default(self):
        """IC-7610 has no [radio].transceiver_count → defaults to 1."""
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.transceiver_count == 1

    def test_transceiver_count_ftx1(self):
        """FTX-1 declares transceiver_count = 2 → must propagate to profile."""
        ftx1_path = RIGS_DIR / "ftx1.toml"
        profile = load_rig(ftx1_path).to_profile()
        assert profile.transceiver_count == 2

    def test_capabilities_frozenset(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert isinstance(profile.capabilities, frozenset)
        assert "audio" in profile.capabilities
        assert "dual_rx" in profile.capabilities

    def test_vfo_main_sub_codes(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.vfo_main_code == 0xD0
        assert profile.vfo_sub_code == 0xD1
        # Legacy alias still works (issue #710)
        assert profile.vfo_swap_code == 0xB0
        # IC-7610 template uses legacy [vfo].swap with scheme=main_sub
        assert profile.swap_main_sub_code == 0xB0
        assert profile.swap_ab_code is None

    def test_vfo_ab_codes(self, tmp_path):
        p = _write_toml(tmp_path, _MINIMAL_TOML)
        profile = load_rig(p).to_profile()
        # ab scheme with no explicit codes → None
        assert profile.vfo_main_code is None
        assert profile.vfo_sub_code is None

    def test_freq_ranges(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert isinstance(profile.freq_ranges, tuple)
        assert len(profile.freq_ranges) == 2
        hf = profile.freq_ranges[0]
        assert isinstance(hf, FreqRangeInfo)
        assert hf.start == 30_000
        assert hf.end == 60_000_000
        assert hf.label == "HF"

    def test_freq_range_bands(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        hf = profile.freq_ranges[0]
        assert len(hf.bands) == 10
        band_160 = hf.bands[0]
        assert isinstance(band_160, BandInfo)
        assert band_160.name == "160m"
        assert band_160.start == 1_800_000
        assert band_160.end == 2_000_000
        assert band_160.default == 1_825_000

    def test_modes(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.modes == (
            "USB",
            "LSB",
            "CW",
            "CW-R",
            "AM",
            "FM",
            "RTTY",
            "RTTY-R",
            "PSK",
            "PSK-R",
        )

    def test_filters(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.filters == ("FIL1", "FIL2", "FIL3")

    def test_filter_config(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.filter_config is not None
        assert profile.filter_config["USB"].defaults == (3000, 2400, 1800)
        assert profile.filter_config["USB-D"].defaults == (3000, 1200, 500)
        assert profile.filter_config["FM"].fixed is True

    def test_model_and_id(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.model == "IC-7610"
        assert profile.id == "icom_ic7610"

    def test_keyboard_config(self):
        profile = load_rig(TEMPLATE_PATH).to_profile()
        assert profile.keyboard is not None
        assert profile.keyboard.leader_timeout_ms == 1000
        assert profile.keyboard.alt_hints is True
        assert any(
            binding.action == "toggle_help" for binding in profile.keyboard.bindings
        )


# ── VFO scheme split (issue #710) ────────────────────────────────


class TestVfoSchemeSplit:
    """Explicit ``swap_ab`` / ``swap_main_sub`` fields + legacy mapping."""

    _MAIN_SUB_SPLIT = """\
    [radio]
    id = "icom_ic7610_test"
    model = "TEST-MAIN-SUB"
    civ_addr = 0x98
    receiver_count = 2
    has_lan = true
    has_wifi = false

    [capabilities]
    features = ["audio", "dual_rx"]

    [modes]
    list = ["USB"]

    [filters]
    list = ["FIL1"]

    [vfo]
    scheme = "main_sub"
    main_select = [0xD0]
    sub_select = [0xD1]
    swap_main_sub = [0xB0]
    equal_main_sub = [0xB1]
    swap_ab = [0x07, 0xB0]
    equal_ab = [0x07, 0xA0]

    [[freq_ranges.ranges]]
    label = "HF"
    start_hz = 30000
    end_hz = 60000000

    [commands]
    get_freq = [0x03]
    """

    def test_new_fields_loaded_into_profile(self, tmp_path):
        p = _write_toml(tmp_path, self._MAIN_SUB_SPLIT)
        profile = load_rig(p).to_profile()
        assert profile.swap_main_sub_code == 0xB0
        assert profile.equal_main_sub_code == 0xB1
        assert profile.swap_ab_code == 0x07
        assert profile.equal_ab_code == 0x07

    def test_legacy_aliases_prefer_main_sub_when_dual(self, tmp_path):
        p = _write_toml(tmp_path, self._MAIN_SUB_SPLIT)
        profile = load_rig(p).to_profile()
        # Legacy alias returns main_sub value when both are set
        assert profile.vfo_swap_code == 0xB0
        assert profile.vfo_equal_code == 0xB1

    def test_legacy_swap_maps_to_main_sub_on_dual_scheme(self, tmp_path):
        toml = """\
        [radio]
        id = "legacy_dual"
        model = "LEGACY-DUAL"
        civ_addr = 0x98
        receiver_count = 2
        has_lan = true
        has_wifi = false

        [capabilities]
        features = ["audio", "dual_rx"]

        [modes]
        list = ["USB"]

        [filters]
        list = ["FIL1"]

        [vfo]
        scheme = "main_sub"
        main_select = [0xD0]
        sub_select = [0xD1]
        swap = [0xB0]
        equal = [0xB1]

        [[freq_ranges.ranges]]
        label = "HF"
        start_hz = 30000
        end_hz = 60000000
        """
        p = _write_toml(tmp_path, toml, name="legacy_dual.toml")
        with pytest.warns(DeprecationWarning, match="issue #710"):
            profile = load_rig(p).to_profile()
        assert profile.swap_main_sub_code == 0xB0
        assert profile.equal_main_sub_code == 0xB1
        assert profile.swap_ab_code is None
        assert profile.equal_ab_code is None
        # Legacy alias still resolves
        assert profile.vfo_swap_code == 0xB0

    def test_legacy_swap_maps_to_ab_on_single_rx_scheme(self, tmp_path):
        toml = """\
        [radio]
        id = "legacy_ab"
        model = "LEGACY-AB"
        civ_addr = 0x94
        receiver_count = 1
        has_lan = true
        has_wifi = false

        [capabilities]
        features = ["audio"]

        [modes]
        list = ["USB"]

        [filters]
        list = ["FIL1"]

        [vfo]
        scheme = "ab"
        swap = [0xB0]
        equal = [0xA0]

        [[freq_ranges.ranges]]
        label = "HF"
        start_hz = 30000
        end_hz = 60000000
        """
        p = _write_toml(tmp_path, toml, name="legacy_ab.toml")
        with pytest.warns(DeprecationWarning, match="issue #710"):
            profile = load_rig(p).to_profile()
        assert profile.swap_ab_code == 0xB0
        assert profile.equal_ab_code == 0xA0
        assert profile.swap_main_sub_code is None
        assert profile.equal_main_sub_code is None
        # Legacy alias still resolves to the ab code
        assert profile.vfo_swap_code == 0xB0
        assert profile.vfo_equal_code == 0xA0

    def test_no_deprecation_when_only_new_keys(self, tmp_path, recwarn):
        p = _write_toml(tmp_path, self._MAIN_SUB_SPLIT, name="new_only.toml")
        load_rig(p)
        assert not [
            w for w in recwarn.list if issubclass(w.category, DeprecationWarning)
        ]


# ── CommandMap ───────────────────────────────────────────────────


class TestCommandMap:
    """CommandMap basic API."""

    def test_get_returns_wire_bytes(self):
        cm = CommandMap({"af_gain": (0x14, 0x01)})
        assert cm.get("af_gain") == (0x14, 0x01)

    def test_get_missing_raises_key_error(self):
        cm = CommandMap({"af_gain": (0x14, 0x01)})
        with pytest.raises(KeyError, match="nonexistent"):
            cm.get("nonexistent")

    def test_has_existing(self):
        cm = CommandMap({"af_gain": (0x14, 0x01)})
        assert cm.has("af_gain") is True

    def test_has_missing(self):
        cm = CommandMap({"af_gain": (0x14, 0x01)})
        assert cm.has("nonexistent") is False

    def test_len(self):
        cm = CommandMap({"a": (0x01,), "b": (0x02,)})
        assert len(cm) == 2

    def test_iter(self):
        cm = CommandMap({"a": (0x01,), "b": (0x02,)})
        assert sorted(cm) == ["a", "b"]

    def test_repr(self):
        cm = CommandMap({"a": (0x01,)})
        assert "CommandMap" in repr(cm)
        assert "1" in repr(cm)


class TestToCommandMap:
    """RigConfig.to_command_map() integration."""

    def test_returns_command_map(self):
        rig = load_rig(TEMPLATE_PATH)
        cm = rig.to_command_map()
        assert isinstance(cm, CommandMap)

    def test_has_expected_commands(self):
        cm = load_rig(TEMPLATE_PATH).to_command_map()
        assert cm.has("get_freq")
        assert cm.has("set_freq")
        assert cm.has("get_af_level")
        assert cm.has("ptt_on")
        assert cm.has("scope_on")

    def test_wire_bytes_correct(self):
        cm = load_rig(TEMPLATE_PATH).to_command_map()
        assert cm.get("get_freq") == (0x03,)
        assert cm.get("get_af_level") == (0x14, 0x01)
        assert cm.get("ptt_on") == (0x1C, 0x00)

    def test_command_count(self):
        cm = load_rig(TEMPLATE_PATH).to_command_map()
        assert len(cm) > 50  # template has ~100 commands


# ── discover_rigs ────────────────────────────────────────────────


class TestDiscoverRigs:
    """discover_rigs() directory scanning."""

    def test_finds_rig_files(self, tmp_path):
        (tmp_path / "ic7300.toml").write_text(_MINIMAL_TOML)
        rigs = discover_rigs(tmp_path)
        assert "IC-7300" in rigs
        assert isinstance(rigs["IC-7300"], RigConfig)

    def test_discovers_ic7610(self):
        # rigs/ has ic7610.toml — should be discovered
        rigs = discover_rigs(RIGS_DIR)
        assert "IC-7610" in rigs

    def test_ignores_underscore_prefix(self, tmp_path):
        # Create a proper rig file and an underscore-prefixed file
        (tmp_path / "ic7300.toml").write_text(_MINIMAL_TOML)
        (tmp_path / "_defaults.toml").write_text(_MINIMAL_TOML)
        rigs = discover_rigs(tmp_path)
        assert "IC-7300" in rigs
        assert len(rigs) == 1  # _defaults.toml was ignored

    def test_returns_dict_keyed_by_model(self, tmp_path):
        (tmp_path / "ic7300.toml").write_text(_MINIMAL_TOML)
        rigs = discover_rigs(tmp_path)
        for model, rig in rigs.items():
            assert rig.model == model

    def test_empty_directory(self, tmp_path):
        rigs = discover_rigs(tmp_path)
        assert rigs == {}
        assert rigs == {}
        assert rigs == {}


class TestCodecPreference:
    """Per-profile [audio] codec_preference override (#797)."""

    def test_single_rx_rigs_pin_mono_first(self):
        """IC-7300/IC-705/IC-9700 all carry mono-first codec preference."""
        for name in ("ic7300.toml", "ic705.toml", "ic9700.toml"):
            rig = load_rig(RIGS_DIR / name)
            assert rig.codec_preference == ("PCM_1CH_16BIT", "ULAW_1CH"), (
                f"{name} must pin mono-first codec_preference"
            )
            profile = rig.to_profile()
            assert profile.codec_preference == ("PCM_1CH_16BIT", "ULAW_1CH")

    def test_ic7610_declares_stereo_pcm_first_override(self):
        """IC-7610 explicitly pins the direct-LAN PCM-first RX preference."""
        rig = load_rig(TEMPLATE_PATH)
        assert rig.codec_preference == (
            "PCM_2CH_16BIT",
            "PCM_1CH_16BIT",
            "ULAW_2CH",
            "ULAW_1CH",
        )
        assert rig.to_profile().codec_preference == (
            "PCM_2CH_16BIT",
            "PCM_1CH_16BIT",
            "ULAW_2CH",
            "ULAW_1CH",
        )

    def test_codec_preference_parses_list_of_strings(self, tmp_path):
        toml = _MINIMAL_TOML + '\n[audio]\ncodec_preference = ["PCM_1CH_16BIT"]\n'
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.codec_preference == ("PCM_1CH_16BIT",)

    def test_missing_audio_section_is_ok(self, tmp_path):
        p = _write_toml(tmp_path, _MINIMAL_TOML)
        rig = load_rig(p)
        assert rig.codec_preference is None

    def test_empty_codec_preference_rejected(self, tmp_path):
        toml = _MINIMAL_TOML + "\n[audio]\ncodec_preference = []\n"
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="must not be empty"):
            load_rig(p)

    def test_unknown_codec_name_rejected(self, tmp_path):
        toml = _MINIMAL_TOML + '\n[audio]\ncodec_preference = ["BOGUS_CODEC"]\n'
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="unknown codec"):
            load_rig(p)

    def test_non_string_codec_entry_rejected(self, tmp_path):
        toml = _MINIMAL_TOML + "\n[audio]\ncodec_preference = [123]\n"
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="list of strings"):
            load_rig(p)

    def test_non_table_audio_section_rejected(self, tmp_path):
        # Insert ``audio = "..."`` before any TOML table so it lands at top level.
        toml = 'audio = "not a table"\n' + _MINIMAL_TOML
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match=r"\[audio\] must be a table"):
            load_rig(p)


class TestAudioPolicy:
    """Per-profile [audio] codec and sample-rate policy (#1470)."""

    def test_ic7610_declares_pcm_first_lan_policy(self):
        rig = load_rig(TEMPLATE_PATH)

        assert rig.codec_preference == (
            "PCM_2CH_16BIT",
            "PCM_1CH_16BIT",
            "ULAW_2CH",
            "ULAW_1CH",
        )
        assert rig.tx_codec == "PCM_1CH_16BIT"
        assert rig.default_sample_rate_hz == 48000
        assert rig.supported_sample_rates_hz is None
        assert rig.sample_rate_by_codec == {
            "PCM_2CH_16BIT": 48000,
            "PCM_1CH_16BIT": 48000,
            "ULAW_2CH": 48000,
            "ULAW_1CH": 48000,
        }
        assert rig.browser_rx_transport == "auto"
        assert rig.browser_rx_transcode_to_opus is True

        profile = rig.to_profile()
        assert profile.codec_preference == (
            "PCM_2CH_16BIT",
            "PCM_1CH_16BIT",
            "ULAW_2CH",
            "ULAW_1CH",
        )
        assert profile.tx_codec == "PCM_1CH_16BIT"
        assert profile.default_sample_rate_hz == 48000
        assert profile.supported_sample_rates_hz is None
        assert profile.sample_rate_by_codec == {
            "PCM_2CH_16BIT": 48000,
            "PCM_1CH_16BIT": 48000,
            "ULAW_2CH": 48000,
            "ULAW_1CH": 48000,
        }
        assert profile.browser_rx_transport == "auto"
        assert profile.browser_rx_transcode_to_opus is True

    def test_ic705_and_ic9700_declare_evidence_backed_mono_lan_policy(self):
        for name in ("ic705.toml", "ic9700.toml"):
            rig = load_rig(RIGS_DIR / name)

            assert rig.codec_preference == ("PCM_1CH_16BIT", "ULAW_1CH")
            assert rig.tx_codec == "PCM_1CH_16BIT"
            assert rig.default_sample_rate_hz is None
            assert rig.supported_sample_rates_hz is None
            assert rig.sample_rate_by_codec is None
            assert rig.browser_rx_transport == "auto"
            assert rig.browser_rx_transcode_to_opus is True

            profile = rig.to_profile()
            assert profile.tx_codec == "PCM_1CH_16BIT"
            assert profile.default_sample_rate_hz is None
            assert profile.sample_rate_by_codec is None
            assert profile.browser_rx_transport == "auto"
            assert profile.browser_rx_transcode_to_opus is True

    def test_full_audio_policy_parses(self, tmp_path):
        toml = (
            _MINIMAL_TOML
            + """

[audio]
codec_preference = ["PCM_2CH_16BIT", "PCM_1CH_16BIT"]
tx_codec = "PCM_1CH_16BIT"
default_sample_rate_hz = 16000
supported_sample_rates_hz = [8000, 16000, 48000]
sample_rate_by_codec = { PCM_2CH_16BIT = 16000, PCM_1CH_16BIT = 16000 }
browser_rx_transport = "auto"
browser_rx_transcode_to_opus = true
"""
        )
        rig = load_rig(_write_toml(tmp_path, toml))

        assert rig.codec_preference == ("PCM_2CH_16BIT", "PCM_1CH_16BIT")
        assert rig.tx_codec == "PCM_1CH_16BIT"
        assert rig.default_sample_rate_hz == 16000
        assert rig.supported_sample_rates_hz == (8000, 16000, 48000)
        assert rig.sample_rate_by_codec == {
            "PCM_2CH_16BIT": 16000,
            "PCM_1CH_16BIT": 16000,
        }
        assert rig.browser_rx_transport == "auto"
        assert rig.browser_rx_transcode_to_opus is True

    def test_existing_profiles_without_new_policy_load_unchanged(self, tmp_path):
        rig = load_rig(_write_toml(tmp_path, _MINIMAL_TOML))
        profile = rig.to_profile()

        assert rig.codec_preference is None
        assert rig.tx_codec is None
        assert rig.default_sample_rate_hz is None
        assert rig.supported_sample_rates_hz is None
        assert rig.sample_rate_by_codec is None
        assert rig.browser_rx_transport is None
        assert rig.browser_rx_transcode_to_opus is None
        assert profile.tx_codec is None
        assert profile.default_sample_rate_hz is None

    def test_unknown_tx_codec_rejected(self, tmp_path):
        toml = _MINIMAL_TOML + '\n[audio]\ntx_codec = "BOGUS_CODEC"\n'
        with pytest.raises(RigLoadError, match=r"\[audio\].tx_codec.*unknown codec"):
            load_rig(_write_toml(tmp_path, toml))

    def test_unsupported_default_sample_rate_rejected(self, tmp_path):
        toml = _MINIMAL_TOML + "\n[audio]\ndefault_sample_rate_hz = 44100\n"
        with pytest.raises(RigLoadError, match="default_sample_rate_hz"):
            load_rig(_write_toml(tmp_path, toml))

    def test_negative_sample_rate_rejected(self, tmp_path):
        toml = _MINIMAL_TOML + "\n[audio]\nsupported_sample_rates_hz = [16000, -1]\n"
        with pytest.raises(RigLoadError, match="supported_sample_rates_hz"):
            load_rig(_write_toml(tmp_path, toml))

    def test_unknown_sample_rate_codec_key_rejected(self, tmp_path):
        toml = (
            _MINIMAL_TOML
            + "\n[audio]\nsample_rate_by_codec = { BOGUS_CODEC = 16000 }\n"
        )
        with pytest.raises(RigLoadError, match="sample_rate_by_codec.*unknown codec"):
            load_rig(_write_toml(tmp_path, toml))

    def test_invalid_browser_transport_rejected(self, tmp_path):
        toml = _MINIMAL_TOML + '\n[audio]\nbrowser_rx_transport = "rtmp"\n'
        with pytest.raises(RigLoadError, match="browser_rx_transport"):
            load_rig(_write_toml(tmp_path, toml))


class TestWriteOnlyControls:
    """[validation].write_only_controls parsing and propagation (MOR-208)."""

    def test_write_only_controls_parsed(self, tmp_path):
        # "scope" is declared in _MINIMAL_TOML's features; mark it write-only.
        toml = _MINIMAL_TOML + '\n[validation]\nwrite_only_controls = ["scope"]\n'
        rig = load_rig(_write_toml(tmp_path, toml))
        assert rig.write_only_controls == ("scope",)
        assert rig.to_profile().write_only_controls == frozenset({"scope"})

    def test_write_only_controls_defaults_empty(self, tmp_path):
        rig = load_rig(_write_toml(tmp_path, _MINIMAL_TOML))
        assert rig.write_only_controls == ()
        assert rig.to_profile().write_only_controls == frozenset()

    def test_write_only_controls_must_be_declared_capability(self, tmp_path):
        # "rit" is NOT in _MINIMAL_TOML's features.
        toml = _MINIMAL_TOML + '\n[validation]\nwrite_only_controls = ["rit"]\n'
        with pytest.raises(RigLoadError, match="rit"):
            load_rig(_write_toml(tmp_path, toml))

    def test_x6200_declares_rit_xit_notch_write_only(self):
        profile = get_radio_profile("X6200")
        assert profile.write_only_controls >= {"rit", "xit", "notch"}
