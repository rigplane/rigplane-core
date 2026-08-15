"""Tests for rig_loader and command_map modules.

TDD: these tests were written FIRST, then the implementation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rigplane.command_map import CommandMap
from rigplane.core.capabilities import CAP_SPEECH, KNOWN_CAPABILITIES
from rigplane.core.tx_interlock_contract import (
    TX_INTERLOCK_COMMAND_FAMILY_METADATA,
    TxInterlockCommandFamily,
    TxInterlockDisposition,
)
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

    def test_tx_interlock_tightening_defaults_empty(self, tmp_path):
        rig = load_rig(_write_toml(tmp_path, _MINIMAL_TOML))

        assert rig.tx_interlock_disposition_overrides == {}
        assert rig.to_profile().tx_interlock_disposition_overrides == {}

    @pytest.mark.parametrize(
        ("header", "family_key"),
        [
            ("[tx_interlock]", '"power-on"'),
            ('["tx_interlock"] # quoted top-level key', "'power-on'"),
        ],
    )
    def test_tx_interlock_parses_tx_safe_to_defer_tightening(
        self, tmp_path, header, family_key
    ):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

{header}
disposition_overrides = {{ {family_key} = "defer" }} # canonical inline mapping
""",
        )

        rig = load_rig(p)
        expected = {
            TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.DEFER,
        }

        assert rig.tx_interlock_disposition_overrides == expected
        assert rig.to_profile().tx_interlock_disposition_overrides == expected

    @pytest.mark.parametrize(
        "family",
        [
            metadata.family.value
            for metadata in TX_INTERLOCK_COMMAND_FAMILY_METADATA
            if metadata.base_disposition is not TxInterlockDisposition.TX_SAFE
        ],
    )
    def test_tx_interlock_rejects_ineligible_base_family(self, tmp_path, family):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[tx_interlock]
disposition_overrides = {{ "{family}" = "defer" }}
""",
        )

        with pytest.raises(
            RigLoadError,
            match=rf"\[tx_interlock\]\.disposition_overrides.*{family}.*not tx-safe",
        ):
            load_rig(p)

    @pytest.mark.parametrize(
        ("declaration", "message"),
        [
            (
                'disposition_overrides = { "unknown-family" = "defer" }',
                "unknown-family",
            ),
            ('disposition_overrides = { "power-on" = "block" }', "must be 'defer'"),
            ('disposition_overrides = { "power-on" = true }', "must be a string"),
            ('disposition_overrides = ["power-on"]', "must be an inline table"),
            ("unexpected = true", "unknown key"),
        ],
    )
    def test_tx_interlock_rejects_invalid_schema(self, tmp_path, declaration, message):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[tx_interlock]
{declaration}
""",
        )

        with pytest.raises(RigLoadError, match=message):
            load_rig(p)

    @pytest.mark.parametrize(
        "value", ['"invalid"', "[{}]", "[{disposition_overrides={}}]"]
    )
    def test_tx_interlock_section_must_be_table(self, tmp_path, value):
        p = _write_toml(tmp_path, f"tx_interlock = {value}\n" + _MINIMAL_TOML)

        with pytest.raises(RigLoadError, match=r"\[tx_interlock\] must be a table"):
            load_rig(p)

    @pytest.mark.parametrize(
        ("declaration", "at_root"),
        [
            ('\n[tx_interlock.disposition_overrides]\n"power-on" = "defer"\n', False),
            (
                '\n["tx_interlock"."disposition_overrides"]\n"power-on" = "defer"\n',
                False,
            ),
            ('\n[tx_interlock."disposition_overrides"]\n"power-on" = "defer"\n', False),
            ('\n[tx_interlock]\ndisposition_overrides."power-on" = "defer"\n', False),
            ('\n[tx_interlock]\ndisposition_overrides.power-on = "defer"\n', False),
            ('tx_interlock.disposition_overrides."power-on" = "defer"\n', True),
            ('tx_interlock.disposition_overrides.power-on = "defer"\n', True),
        ],
    )
    def test_tx_interlock_rejects_non_inline_override_encodings(
        self, tmp_path, declaration, at_root
    ):
        content = (
            declaration + _MINIMAL_TOML if at_root else _MINIMAL_TOML + declaration
        )
        p = _write_toml(tmp_path, content)

        with pytest.raises(
            RigLoadError,
            match=r"\[tx_interlock\]\.disposition_overrides must use inline table syntax",
        ):
            load_rig(p)

    @pytest.mark.parametrize(
        "label",
        [
            '"[tx_interlock.disposition_overrides]"',
            '\'disposition_overrides."power-on" = "defer"\'',
            '"""multiline\n[tx_interlock.disposition_overrides]\n"""',
            "'''multiline\ntx_interlock.disposition_overrides.power-on = 'defer'\n'''",
        ],
    )
    def test_tx_interlock_shape_guard_ignores_string_content(self, tmp_path, label):
        toml = _MINIMAL_TOML.replace('label = "HF"', f"label = {label}")

        rig = load_rig(_write_toml(tmp_path, toml))

        assert rig.tx_interlock_disposition_overrides == {}

    def test_tx_interlock_shape_guard_ignores_comments(self, tmp_path):
        p = _write_toml(
            tmp_path,
            "# [tx_interlock.disposition_overrides]\n"
            '# tx_interlock.disposition_overrides."power-on" = "defer"\n'
            + _MINIMAL_TOML,
        )

        assert load_rig(p).tx_interlock_disposition_overrides == {}

    @pytest.mark.parametrize("key", ["tx_interlock", '"tx_interlock"'])
    def test_tx_interlock_rejects_root_outer_inline_table(self, tmp_path, key):
        content = (
            f'{key}={{disposition_overrides={{"power-on"="defer"}}}}\n' + _MINIMAL_TOML
        )
        p = _write_toml(tmp_path, content)

        with pytest.raises(RigLoadError, match="must use inline table syntax"):
            load_rig(p)

    def test_tx_interlock_shape_guard_tracks_multiline_array_context(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[metadata]
values = [
    ["tx_interlock"]
]
disposition_overrides.label = "not policy"
""",
        )

        assert load_rig(p).tx_interlock_disposition_overrides == {}

    def test_load_minimal_power_max_watts(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[power]
max_watts = 100
""",
        )

        rig = load_rig(p)
        profile = rig.to_profile()

        assert rig.max_watts == 100
        assert profile.max_watts == 100

    def test_state_acquisition_provider_must_be_string(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[state_acquisition]
provider = 123
""",
        )
        with pytest.raises(
            RigLoadError,
            match=r"\[state_acquisition\].*provider must be a string",
        ):
            load_rig(p)

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

    def test_speech_capability_loads(self, tmp_path):
        toml = _MINIMAL_TOML.replace(
            'features = ["audio", "scope", "meters", "tx"]',
            'features = ["audio", "speech"]',
        )
        rig = load_rig(_write_toml(tmp_path, toml))
        assert "speech" in rig.capabilities

    def test_speech_capability_is_exported_and_known(self):
        assert CAP_SPEECH == "speech"
        assert CAP_SPEECH in KNOWN_CAPABILITIES

    def test_invalid_vfo_scheme(self, tmp_path):
        toml = _MINIMAL_TOML.replace('scheme = "ab"', 'scheme = "xyz"')
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="vfo.*scheme"):
            load_rig(p)

    # ── [agc] domain declaration (MOR-1522) ─────────────────────────

    def test_agc_modes_and_labels_load(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[agc]
modes = [1, 2, 3]
labels = { "1" = "FAST", "2" = "MID", "3" = "SLOW" }
""",
        )
        rig = load_rig(p)
        assert rig.agc_modes == (1, 2, 3)
        assert rig.agc_labels == {"1": "FAST", "2": "MID", "3": "SLOW"}

    def test_agc_section_rejects_unknown_key(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[agc]
mode = [1, 2, 3]
""",
        )
        with pytest.raises(RigLoadError, match=r"\[agc\].*unknown key"):
            load_rig(p)

    def test_agc_modes_rejects_empty_list(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[agc]
modes = []
""",
        )
        with pytest.raises(RigLoadError, match=r"\[agc\]\.modes"):
            load_rig(p)

    def test_agc_modes_rejects_non_integer_entries(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[agc]
modes = [1, "FAST", 3]
""",
        )
        with pytest.raises(RigLoadError, match=r"\[agc\]\.modes"):
            load_rig(p)

    def test_agc_labels_rejects_orphan_key_not_in_modes(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[agc]
modes = [1, 2, 3]
labels = { "1" = "FAST", "2" = "MID", "3" = "SLOW", "9" = "PHANTOM" }
""",
        )
        with pytest.raises(RigLoadError, match=r"\[agc\]\.labels.*9"):
            load_rig(p)

    def test_agc_labels_rejects_declared_without_modes(self, tmp_path):
        """MOR-1522 R1 (B2): [agc].labels with no [agc].modes must not load
        silently — that would yield a capability-present radio with an
        empty domain, short-circuiting both runtime validation seats
        (``agc_modes is not None`` in ``IcomRadio.set_agc`` /
        ``YaesuCatRadio.set_agc``)."""
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[agc]
labels = { "1" = "FAST", "2" = "MID", "3" = "SLOW" }
""",
        )
        with pytest.raises(
            RigLoadError, match=r"\[agc\]\.labels declared without \[agc\]\.modes"
        ):
            load_rig(p)

    def test_agc_capability_absent_declares_no_domain(self, tmp_path):
        """A radio that never mentions AGC at all — no capability, no
        [agc] section — is valid: capability-absent hides the selector
        (MOR-1494 pattern), it is not a malformed declaration."""
        p = _write_toml(tmp_path, _MINIMAL_TOML)
        rig = load_rig(p)
        assert rig.agc_modes is None
        assert rig.agc_labels is None

    def test_rf_sql_control_model_defaults_to_separate(self, tmp_path):
        p = _write_toml(tmp_path, _MINIMAL_TOML)
        rig = load_rig(p)
        assert rig.rf_sql_control_model == "separate"
        assert rig.to_profile().rf_sql_control_model == "separate"

    def test_rf_sql_control_model_combined(self, tmp_path):
        toml = _MINIMAL_TOML.replace(
            'features = ["audio", "scope", "meters", "tx"]',
            'features = ["audio", "scope", "meters", "tx"]\n'
            'rf_sql_control_model = "combined"',
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)
        assert rig.rf_sql_control_model == "combined"
        assert rig.to_profile().rf_sql_control_model == "combined"

    def test_rf_sql_control_model_invalid(self, tmp_path):
        toml = _MINIMAL_TOML.replace(
            'features = ["audio", "scope", "meters", "tx"]',
            'features = ["audio", "scope", "meters", "tx"]\n'
            'rf_sql_control_model = "concentric"',
        )
        p = _write_toml(tmp_path, toml)
        with pytest.raises(RigLoadError, match="rf_sql_control_model"):
            load_rig(p)

    def test_ic7300_declares_combined_rf_sql_control_model(self):
        rig = load_rig(RIGS_DIR / "ic7300.toml")
        assert rig.rf_sql_control_model == "combined"

    def test_ic7610_stays_separate_rf_sql_control_model(self):
        rig = load_rig(RIGS_DIR / "ic7610.toml")
        assert rig.rf_sql_control_model == "separate"

    @pytest.mark.parametrize(
        ("scheme", "receiver_count"),
        [
            ("single", 1),
            ("ab", 1),
            ("ab_shared", 2),
            ("main_sub", 2),
        ],
    )
    def test_valid_receiver_count_vfo_scheme_pairs(
        self,
        tmp_path,
        scheme,
        receiver_count,
    ):
        toml = _MINIMAL_TOML.replace(
            "receiver_count = 1",
            f"receiver_count = {receiver_count}",
        ).replace('scheme = "ab"', f'scheme = "{scheme}"')
        rig = load_rig(_write_toml(tmp_path, toml))

        assert rig.receiver_count == receiver_count
        assert rig.vfo_scheme == scheme

    @pytest.mark.parametrize(
        ("scheme", "receiver_count", "expected_count"),
        [
            ("single", 2, 1),
            ("ab", 2, 1),
            ("ab_shared", 1, 2),
            ("main_sub", 1, 2),
            *[
                (scheme, receiver_count, expected_count)
                for scheme, expected_count in [
                    ("single", 1),
                    ("ab", 1),
                    ("ab_shared", 2),
                    ("main_sub", 2),
                ]
                for receiver_count in (0, -1, 3)
            ],
        ],
    )
    def test_invalid_receiver_count_vfo_scheme_pairs(
        self,
        tmp_path,
        scheme,
        receiver_count,
        expected_count,
    ):
        toml = _MINIMAL_TOML.replace(
            "receiver_count = 1",
            f"receiver_count = {receiver_count}",
        ).replace('scheme = "ab"', f'scheme = "{scheme}"')

        with pytest.raises(
            RigLoadError,
            match=(
                rf"\[radio\]\.receiver_count = {receiver_count} is incompatible "
                rf"with \[vfo\]\.scheme = '{scheme}'; expected {expected_count}"
            ),
        ):
            load_rig(_write_toml(tmp_path, toml))

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

    def test_default_mode_bindings_follow_loaded_profile_modes(self):
        no_psk = load_rig(RIGS_DIR / "ic7300.toml")
        assert no_psk.keyboard is not None
        no_psk_ids = {binding.id for binding in no_psk.keyboard.bindings}
        assert {"mode-lsb", "mode-usb"} <= no_psk_ids
        assert {"mode-psk", "mode-pskr"}.isdisjoint(no_psk_ids)

        psk_capable = load_rig(RIGS_DIR / "ic7610.toml")
        assert psk_capable.keyboard is not None
        psk_capable_ids = {binding.id for binding in psk_capable.keyboard.bindings}
        assert {"mode-psk", "mode-pskr"} <= psk_capable_ids

    def test_mode_select_with_non_string_mode_preserves_parser_behavior(self, tmp_path):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + """

[ui.keyboard]
[[ui.keyboard.bindings]]
id = "non-string-mode"
key = "F1"
action = "mode_select"
[ui.keyboard.bindings.params]
mode = 42
""",
        )

        rig = load_rig(p)

        assert rig.keyboard is not None
        assert rig.keyboard.bindings[0].params == {"mode": 42}


class TestControlDomainSchema:
    _LINEAR = """\
raw_min = 0
raw_max = 10
raw_step = 2
raw_origin = 0
display_min = 0.0
display_max = 1.0
display_step = 0.2
display_origin = 0.0
display_unit = "ratio"
mapping = "linear"
quantization = "nearest_ties_up"
restoration = "exact"
"""

    def _load(self, tmp_path, declaration: str):
        return load_rig(
            _write_toml(
                tmp_path,
                _MINIMAL_TOML + "\n[controls.test_control]\n" + declaration,
            )
        )

    @pytest.mark.parametrize(
        ("mapping", "declaration"),
        [
            (
                "identity",
                _LINEAR.replace("display_max = 1.0", "display_max = 10")
                .replace("display_step = 0.2", "display_step = 2")
                .replace('display_unit = "ratio"', 'display_unit = "dimensionless"')
                .replace('mapping = "linear"', 'mapping = "identity"'),
            ),
            ("linear", _LINEAR),
            (
                "centered",
                _LINEAR.replace("raw_min = 0", "raw_min = -10")
                .replace("display_min = 0.0", "display_min = -1.0")
                .replace('mapping = "linear"', 'mapping = "centered"')
                + "raw_center = 0\ndisplay_center = 0.0\n",
            ),
        ],
    )
    def test_scalar_domain_is_private_and_not_serialized(
        self, tmp_path, mapping, declaration
    ):
        rig = self._load(tmp_path, declaration)

        assert rig._control_domains["test_control"]["mapping"] == mapping
        assert rig.controls is None
        assert rig.to_profile().controls is None

    @pytest.mark.parametrize(
        ("old", "new", "message"),
        [
            ("raw_min = 0", "raw_min = 1", "raw_min must lie"),
            ("raw_max = 10", "raw_max = 9", "raw_max must lie"),
            (
                "raw_max = 10",
                "raw_max = 9007199254740991",
                "raw_max must lie",
            ),
            ("display_min = 0.0", "display_min = 0.05", "display_min must lie"),
            (
                "display_max = 1.0",
                "display_max = 1000000000000.05",
                "display_max must lie",
            ),
        ],
    )
    def test_rejects_off_lattice_endpoints_at_small_and_large_indices(
        self, tmp_path, old, new, message
    ):
        with pytest.raises(RigLoadError, match=message):
            self._load(tmp_path, self._LINEAR.replace(old, new))

    @pytest.mark.parametrize(
        ("centers", "message"),
        [
            ("raw_center = 3\ndisplay_center = 0.0\n", "raw_center must lie"),
            ("raw_center = 0\ndisplay_center = 0.1\n", "display_center must lie"),
        ],
    )
    def test_rejects_off_lattice_centers(self, tmp_path, centers, message):
        declaration = self._LINEAR.replace('mapping = "linear"', 'mapping = "centered"')
        with pytest.raises(RigLoadError, match=message):
            self._load(tmp_path, declaration + centers)

    @pytest.mark.parametrize(
        ("extra", "accepted"),
        [
            ("range_min = 0\nrange_max = 10\n", True),
            ("range_min = 0\nrange_max = 11\n", False),
        ],
    )
    def test_legacy_range_must_be_formally_equivalent(self, tmp_path, extra, accepted):
        declaration = self._LINEAR + extra
        if accepted:
            assert self._load(tmp_path, declaration)._control_domains is not None
        else:
            with pytest.raises(RigLoadError, match="legacy range.*raw bounds"):
                self._load(tmp_path, declaration)

    @pytest.mark.parametrize("unit", [None, "", "   "])
    def test_explicit_domain_requires_non_empty_display_unit(self, tmp_path, unit):
        declaration = self._LINEAR
        if unit is None:
            declaration = declaration.replace('display_unit = "ratio"\n', "")
        else:
            declaration = declaration.replace(
                'display_unit = "ratio"', f'display_unit = "{unit}"'
            )
        with pytest.raises(RigLoadError, match="display_unit.*non-empty"):
            self._load(tmp_path, declaration)

    def test_lookup_is_explicitly_deferred(self, tmp_path):
        declaration = self._LINEAR.replace('mapping = "linear"', 'mapping = "lookup"')
        with pytest.raises(RigLoadError, match="lookup.*MOR-1708"):
            self._load(tmp_path, declaration)

    def test_shipped_legacy_profiles_remain_publicly_shape_compatible(self):
        paths = (
            path for path in RIGS_DIR.glob("*.toml") if not path.name.startswith("_")
        )
        for path in sorted(paths):
            rig = load_rig(path)
            assert rig._control_domains is None, path.name
            assert rig.to_profile().controls == rig.controls, path.name


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

    def test_ic7610_drops_dead_tone_commands(self):
        """MOR-682: IC-7610 has no FM-repeater tone capability, so the
        repeater-tone / TSQL command entries must not be in the command map."""
        cm = load_rig(TEMPLATE_PATH).to_command_map()
        for key in (
            "get_repeater_tone",
            "set_repeater_tone",
            "get_repeater_tsql",
            "set_repeater_tsql",
            "get_tone_freq",
            "set_tone_freq",
            "get_tsql_freq",
            "set_tsql_freq",
        ):
            assert not cm.has(key), f"dead tone command {key!r} still present"


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

    def test_x6200_drops_tone_family_and_does_not_declare_filter_width(self):
        # MOR-683: a live X6200 capture (CI-V 0xA4) confirmed the tone family
        # (repeater_tone / tsql, driving 0x16 0x42/0x43 + 0x1B) times out, so
        # those caps are dropped → the four tone RMVR checks resolve
        # UNSUPPORTED instead of FAIL. filter_width stays UNDECLARED: the same
        # live capture showed 0x1A 0x03 cannot be round-tripped (get reads a
        # passband index, the USB setter guards 50-9999 Hz) and Hamlib marks
        # x1ax03_supported=0. rit/xit/notch and the DSP toggles stay untouched.
        caps = get_radio_profile("X6200").capabilities
        assert "repeater_tone" not in caps
        assert "tsql" not in caps
        assert "filter_width" not in caps
        assert {"rit", "xit", "notch", "nr", "nb", "compressor"} <= caps

    def test_x6200_filter_width_is_read_only_raw_byte_profile_mapping(self):
        # MOR-706: X6200 0x1A/0x03 returns a single raw byte index for the
        # active FIL slot width. Keep the public capability undeclared because
        # the Hz setter is not live-proven, but preserve the GET command so
        # runtime reads can use the profile mapping.
        rig = load_rig(RIGS_DIR / "x6200.toml")
        profile = rig.to_profile()

        assert profile.filter_width_encoding == "raw_byte_index"
        assert "filter_width" not in profile.capabilities
        assert "get_filter_width" in profile.command_names
        assert "set_filter_width" not in profile.command_names

        rule = profile.resolve_filter_rule("USB")
        assert rule is not None
        assert rule.segments
        for fixed_mode in ("AM", "FM"):
            fixed_rule = profile.resolve_filter_rule(fixed_mode)
            assert fixed_rule is not None
            assert fixed_rule.fixed is True

    def test_x6200_drops_over_declared_pbt(self):
        # MOR-699: a live X6200 probe (CI-V 0xA4) showed twin-PBT (get_pbt_inner
        # 0x14 0x07 and get_pbt_outer 0x14 0x08) time out in BOTH AM and USB
        # modes — the front-panel PBT is not exposed over CI-V. The cap is
        # over-declared (Hamlib xiegu.c declares it without a round-trip), so it
        # is dropped → no pbt check is generated (cap undeclared) instead of the
        # earlier spurious pbt.presence dry-run pass. Kept controls stay intact.
        caps = get_radio_profile("X6200").capabilities
        assert "pbt" not in caps
        assert {"rit", "xit", "notch", "nr", "nb", "compressor"} <= caps

    def test_x6100_drops_unsupported_tone_and_pbt(self):
        # MOR-634: the X6100 over-declared repeater_tone / tsql / pbt. By the
        # X6100/X6200 shared-firmware inference (Hamlib xiegu.c reuses the same
        # x6100_priv_caps struct for RIG_MODEL_X6200=3091) plus live X6200
        # evidence — tone 0x16 0x42/0x43 + 0x1B (MOR-683) and pbt 0x14 07/08
        # (MOR-699) both time out on real hardware — these caps are dropped.
        # NOT live-confirmed on an X6100 (no X6100 hardware exists to capture);
        # this is a conservative by-inference alignment pending direct
        # confirmation. The profile must still load and keep its other caps.
        profile = get_radio_profile("X6100")
        caps = profile.capabilities
        assert "repeater_tone" not in caps
        assert "tsql" not in caps
        assert "pbt" not in caps
        # filter_width is already (correctly) not advertised — keep it so.
        assert "filter_width" not in caps
        # Unrelated caps stay untouched.
        assert {"rit", "xit", "notch", "nr", "nb", "compressor"} <= caps


# ── AGC domain declaration, table-driven over every shipped profile ──────
# (MOR-1522). "Shipped profile" = every rigs/*.toml except the UI-only
# _keyboard-default.toml, taken from the directory listing itself rather
# than a hand-maintained name list, so a new profile can't silently land
# in the ambiguous middle this test forbids.

_SHIPPED_RIG_TOMLS = sorted(
    p for p in RIGS_DIR.glob("*.toml") if p.name != "_keyboard-default.toml"
)


class TestAgcDomainDeclaredOrCapabilityAbsent:
    """Every shipped profile must land on one of exactly two sides:

    (a) it does not declare the ``agc`` capability at all — the
        capability-absent selector-hiding pattern (MOR-1494) — or
    (b) it declares ``agc`` AND a non-empty ``[agc] modes`` domain that
        every declared value can be encoded into a legal wire command.

    No profile may declare the capability without a domain, or a domain
    without the capability.
    """

    @pytest.mark.parametrize("toml_path", _SHIPPED_RIG_TOMLS, ids=lambda p: p.stem)
    def test_agc_capability_and_domain_agree(self, toml_path):
        rig = load_rig(toml_path)
        has_agc_capability = "agc" in rig.capabilities

        if not has_agc_capability:
            assert rig.agc_modes is None, (
                f"{toml_path.name}: declares [agc] modes without the "
                f"'agc' capability feature"
            )
            return

        assert rig.agc_modes, (
            f"{toml_path.name}: declares the 'agc' capability but no "
            f"(or an empty) [agc] modes domain"
        )

    @pytest.mark.parametrize(
        "toml_path",
        [p for p in _SHIPPED_RIG_TOMLS if load_rig(p).protocol_type == "civ"],
        ids=lambda p: p.stem,
    )
    def test_civ_agc_modes_encode_to_a_legal_command(self, toml_path):
        """Every declared mode for a CI-V family radio must round-trip
        through the real wire-command builder without raising — the
        profile's domain must map to a legal command, not just a legal
        Python int (MOR-1522)."""
        from rigplane.commands.dsp import set_agc

        rig = load_rig(toml_path)
        if rig.agc_modes is None:
            pytest.skip(f"{toml_path.name}: no agc capability")
        for mode in rig.agc_modes:
            civ = set_agc(mode, to_addr=rig.civ_addr)
            # All shipped AGC domains are single-digit (0-9), so the BCD
            # byte equals the raw mode value — this is both the
            # "encodes without raising" check and a byte-shape pin.
            assert civ.endswith(bytes([0x16, 0x12, mode, 0xFD])), (
                f"{toml_path.name}: mode {mode} did not encode to the "
                f"expected 0x16 0x12 {mode:#04x} wire command"
            )

    def test_ic7300_declares_exactly_fast_mid_slow_no_off(self):
        """The MOR-1522 regression pin: IC-7300 must show FAST/MID/SLOW
        only. Confirmed against docs/validation/cat-audits/ic7300.md
        (CI-V 0x16 0x12: 1=FAST, 2=MID, 3=SLOW, no OFF)."""
        rig = load_rig(RIGS_DIR / "ic7300.toml")
        assert rig.agc_modes == (1, 2, 3)
        assert 0 not in rig.agc_modes

    def test_x6200_keeps_its_declared_off(self):
        """A profile that DOES have AGC OFF must keep it — the fix must not
        over-correct into dropping OFF everywhere. X6200 CI-V doc (PDF page
        8): 0x16 0x12 0x00..0x03 = off/fast/slow/auto."""
        rig = load_rig(RIGS_DIR / "x6200.toml")
        assert 0 in rig.agc_modes
        assert rig.agc_labels["0"] == "OFF"

    def test_ftx1_keeps_its_declared_off(self):
        """FTX-1 (live bench hardware) also legitimately has AGC OFF."""
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert 0 in rig.agc_modes
        assert rig.agc_labels["0"] == "OFF"

    def test_ftx1_agc_auto_labels_are_short_form(self):
        """MOR-1547: FTX-1's auto-selected AGC modes (4/5/6) must declare the
        short "A-F"/"A-M"/"A-S" form, not "A-FAST"/"A-MID"/"A-SLOW".

        The long form's 6-character body, prefixed "AGC " by the amber-lcd
        skin's AmberCockpit/AmberScope status chip, produced a 10-character
        chip wider than any sibling chip sharing AmberIndStrip's
        flex-wrap + overflow:hidden row (AmberIndStrip.svelte:68-128) — the
        wrapped second row was silently clipped. This is the real-profile
        witness for that fix (a hand-copied constant in a frontend component
        test cannot catch a regression to the TOML itself — reverting these
        three lines must fail THIS test).
        """
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert rig.agc_labels["4"] == "A-F"
        assert rig.agc_labels["5"] == "A-M"
        assert rig.agc_labels["6"] == "A-S"
        # The amber-chip width invariant itself: every declared FTX-1 AGC
        # label body must fit inside the 4-character FAST/SLOW budget every
        # other shipped [agc.labels] table (ic7300/ic7610/ic705/ic9700/x6200)
        # already renders inside without wrapping the AmberIndStrip row.
        assert max(len(v) for v in rig.agc_labels.values()) <= 4

    def test_x6100_and_tx500_declare_no_agc_capability(self):
        """Neither has AGC wired at all today (X6100: no confirmed hardware
        capture; TX-500: no backend/CAT implementation at all) — capability-
        absent is correct for both, not an empty/invented domain."""
        for name in ("x6100", "tx500"):
            rig = load_rig(RIGS_DIR / f"{name}.toml")
            assert "agc" not in rig.capabilities
            assert rig.agc_modes is None


# ── Preamp domain declaration, table-driven over every shipped profile ───
# (MOR-1523, mirrors MOR-1522's AGC table above). Reuses _SHIPPED_RIG_TOMLS.


class TestPreampDomainDeclaredOrCapabilityAbsent:
    """Every shipped profile must land on one of exactly two sides:

    (a) it does not declare the ``preamp`` capability at all — the
        capability-absent selector-hiding pattern (MOR-1494) — or
    (b) it declares ``preamp`` AND a non-empty ``[preamp] values`` domain
        that every declared value can be encoded into a legal wire command.

    No profile may declare the capability without a domain, or a domain
    without the capability.
    """

    @pytest.mark.parametrize("toml_path", _SHIPPED_RIG_TOMLS, ids=lambda p: p.stem)
    def test_preamp_capability_and_domain_agree(self, toml_path):
        rig = load_rig(toml_path)
        has_preamp_capability = "preamp" in rig.capabilities

        if not has_preamp_capability:
            assert rig.pre_values is None, (
                f"{toml_path.name}: declares [preamp] values without the "
                f"'preamp' capability feature"
            )
            return

        assert rig.pre_values, (
            f"{toml_path.name}: declares the 'preamp' capability but no "
            f"(or an empty) [preamp] values domain"
        )

    @pytest.mark.parametrize(
        "toml_path",
        [p for p in _SHIPPED_RIG_TOMLS if load_rig(p).protocol_type == "civ"],
        ids=lambda p: p.stem,
    )
    def test_civ_preamp_values_encode_to_a_legal_command(self, toml_path):
        """Every declared level for a CI-V family radio must round-trip
        through the real wire-command builder without raising — the
        profile's domain must map to a legal command, not just a legal
        Python int (MOR-1523)."""
        from rigplane.commands.dsp import set_preamp

        rig = load_rig(toml_path)
        if rig.pre_values is None:
            pytest.skip(f"{toml_path.name}: no preamp capability")
        for level in rig.pre_values:
            civ = set_preamp(level, to_addr=rig.civ_addr)
            # All shipped preamp domains are single-digit (0-9), so the BCD
            # byte equals the raw level value.
            assert civ.endswith(bytes([0x16, 0x02, level, 0xFD])), (
                f"{toml_path.name}: level {level} did not encode to the "
                f"expected 0x16 0x02 {level:#04x} wire command"
            )

    def test_ic7300_declares_off_amp1_amp2(self):
        """IC-7300 P.AMP: 0=OFF, 1=P.AMP1, 2=P.AMP2 (CI-V 0x16 0x02, per
        rigs/ic7300.toml's [preamp] comment)."""
        rig = load_rig(RIGS_DIR / "ic7300.toml")
        assert rig.pre_values == (0, 1, 2)

    def test_x6200_has_no_second_preamp_stage(self):
        """The X6200 declares only [0, 1] — it has no P.AMP2 hardware stage,
        unlike the IC-7300 family's [0, 1, 2]. Domain-shape difference the
        validation seat must honor, not flatten to a universal 3-state
        enum."""
        rig = load_rig(RIGS_DIR / "x6200.toml")
        assert rig.pre_values == (0, 1)

    def test_ftx1_declares_ipo_amp1_amp2_with_labels(self):
        """FTX-1 (live bench hardware) declares [preamp] values = [0, 1, 2]
        with IPO/AMP1/AMP2 labels (rigs/ftx1.toml:430-436)."""
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert rig.pre_values == (0, 1, 2)


# ── MOR-1534: break_in/notch-width/ssb_tx_bw/filter_shape were each either
# declared in TOML but never parsed (break_in, notch width), hardcoded via
# an IC-7610-specific enum with no profile domain at all (ssb_tx_bw), or had
# no TOML domain to begin with (filter_shape). All four now route through
# ``_parse_enumerated_domain`` — the same shared fail-loud contract
# ``[agc] modes/labels`` (MOR-1522) established, generalized rather than
# copy-pasted four times.


class TestEnumeratedDomainMalformedDeclarations:
    """One shared parametrized suite over all four [section] value/label
    pairs, instead of four near-identical malformed-declaration classes —
    they all share the same ``_parse_enumerated_domain`` implementation."""

    _SECTIONS = [
        ("break_in", "values", "labels"),
        ("notch", "width_values", "width_labels"),
        ("ssb_tx_bw", "values", "labels"),
        ("filter_shape", "values", "labels"),
    ]

    @pytest.mark.parametrize(
        ("section", "values_key", "labels_key"), _SECTIONS, ids=lambda t: t
    )
    def test_domain_values_and_labels_load(
        self, tmp_path, section, values_key, labels_key
    ):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[{section}]
{values_key} = [0, 1, 2]
{labels_key} = {{ "0" = "A", "1" = "B", "2" = "C" }}
""",
        )
        rig = load_rig(p)
        values_attr = {
            "break_in": "break_in_modes",
            "notch": "notch_width_values",
            "ssb_tx_bw": "ssb_tx_bw_values",
            "filter_shape": "filter_shape_values",
        }[section]
        labels_attr = {
            "break_in": "break_in_labels",
            "notch": "notch_width_labels",
            "ssb_tx_bw": "ssb_tx_bw_labels",
            "filter_shape": "filter_shape_labels",
        }[section]
        assert getattr(rig, values_attr) == (0, 1, 2)
        assert getattr(rig, labels_attr) == {"0": "A", "1": "B", "2": "C"}

    @pytest.mark.parametrize(
        ("section", "values_key", "labels_key"), _SECTIONS, ids=lambda t: t
    )
    def test_domain_rejects_unknown_key(
        self, tmp_path, section, values_key, labels_key
    ):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[{section}]
bogus = [0, 1]
""",
        )
        with pytest.raises(RigLoadError, match=rf"\[{section}\].*unknown key"):
            load_rig(p)

    @pytest.mark.parametrize(
        ("section", "values_key", "labels_key"), _SECTIONS, ids=lambda t: t
    )
    def test_domain_rejects_empty_values_list(
        self, tmp_path, section, values_key, labels_key
    ):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[{section}]
{values_key} = []
""",
        )
        with pytest.raises(RigLoadError, match=rf"\[{section}\]\.{values_key}"):
            load_rig(p)

    @pytest.mark.parametrize(
        ("section", "values_key", "labels_key"), _SECTIONS, ids=lambda t: t
    )
    def test_domain_rejects_non_integer_entries(
        self, tmp_path, section, values_key, labels_key
    ):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[{section}]
{values_key} = [0, "WIDE", 2]
""",
        )
        with pytest.raises(RigLoadError, match=rf"\[{section}\]\.{values_key}"):
            load_rig(p)

    @pytest.mark.parametrize(
        ("section", "values_key", "labels_key"), _SECTIONS, ids=lambda t: t
    )
    def test_domain_rejects_orphan_label_key(
        self, tmp_path, section, values_key, labels_key
    ):
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[{section}]
{values_key} = [0, 1, 2]
{labels_key} = {{ "0" = "A", "1" = "B", "2" = "C", "9" = "PHANTOM" }}
""",
        )
        with pytest.raises(RigLoadError, match=rf"\[{section}\]\.{labels_key}.*9"):
            load_rig(p)

    @pytest.mark.parametrize(
        ("section", "values_key", "labels_key"), _SECTIONS, ids=lambda t: t
    )
    def test_domain_rejects_labels_declared_without_values(
        self, tmp_path, section, values_key, labels_key
    ):
        """MOR-1522 R1 (B2) hole class, generalized: labels with no values
        must not load silently — that would yield a capability-present
        control with an empty domain, short-circuiting the runtime
        validation seat's ``is not None`` guard into a permissive no-op."""
        p = _write_toml(
            tmp_path,
            _MINIMAL_TOML
            + f"""

[{section}]
{labels_key} = {{ "0" = "A", "1" = "B", "2" = "C" }}
""",
        )
        with pytest.raises(
            RigLoadError,
            match=rf"\[{section}\]\.{labels_key} declared without \[{section}\]\.{values_key}",
        ):
            load_rig(p)

    @pytest.mark.parametrize(
        ("section", "values_key", "labels_key"), _SECTIONS, ids=lambda t: t
    )
    def test_domain_absent_when_section_absent(
        self, tmp_path, section, values_key, labels_key
    ):
        p = _write_toml(tmp_path, _MINIMAL_TOML)
        rig = load_rig(p)
        values_attr = {
            "break_in": "break_in_modes",
            "notch": "notch_width_values",
            "ssb_tx_bw": "ssb_tx_bw_values",
            "filter_shape": "filter_shape_values",
        }[section]
        assert getattr(rig, values_attr) is None


class TestBreakInDomainDeclaredOrDocumentedAbsent:
    """Every shipped profile must land on one of exactly two sides:

    (a) it does not declare the ``break_in`` capability at all, or
    (b) it declares ``break_in`` AND a non-empty ``[break_in] values``
        domain — UNLESS it is one of the documented exceptions below, each
        of which declares the capability without a domain for its own
        reason (not an oversight):

        - ``ftx1``: Yaesu CAT break-in is boolean on/off, not an enumerated
          OFF/SEMI/FULL register — ``YaesuCatRadio.set_break_in`` collapses
          any non-OFF value to ON by design (issue #1100), no TOML domain
          needed.
        - ``x6100``/``x6200``: advertise the capability but the backend's
          break-in opcode (0x16 0x47, IC-7610's CW BK-IN register) is not
          confirmed in either radio's documented CI-V table
          (docs/validation/cat-audits/x6200.md lines 56/109/128/132; no
          X6100 cat-audit exists at all). No trustworthy in-repo source for
          a value domain — ``CoreRadio.set_break_in`` fails loud instead.
    """

    _NO_DOMAIN_BY_DESIGN = frozenset({"ftx1", "x6100", "x6200"})

    @pytest.mark.parametrize("toml_path", _SHIPPED_RIG_TOMLS, ids=lambda p: p.stem)
    def test_break_in_capability_and_domain_agree(self, toml_path):
        rig = load_rig(toml_path)
        has_break_in_capability = "break_in" in rig.capabilities

        if not has_break_in_capability:
            assert rig.break_in_modes is None, (
                f"{toml_path.name}: declares [break_in] values without the "
                f"'break_in' capability feature"
            )
            return

        if toml_path.stem in self._NO_DOMAIN_BY_DESIGN:
            assert rig.break_in_modes is None, (
                f"{toml_path.name}: now declares a [break_in] domain — "
                "update TestBreakInDomainDeclaredOrDocumentedAbsent's "
                "_NO_DOMAIN_BY_DESIGN exception list to match"
            )
            return

        assert rig.break_in_modes, (
            f"{toml_path.name}: declares the 'break_in' capability but no "
            f"(or an empty) [break_in] values domain"
        )

    def test_ic7610_family_declares_off_semi_full(self):
        """IC-705/IC-7300/IC-9700/IC-7610 all declare the same CI-V 0x16
        0x47 domain: 0=OFF, 1=SEMI, 2=FULL."""
        for name in ("ic705", "ic7300", "ic9700", "ic7610"):
            rig = load_rig(RIGS_DIR / f"{name}.toml")
            assert rig.break_in_modes == (0, 1, 2), name
            assert rig.break_in_labels == {
                "0": "OFF",
                "1": "SEMI",
                "2": "FULL",
            }, name

    def test_documented_exceptions_declare_capability_without_domain(self):
        for name in ("ftx1", "x6100", "x6200"):
            rig = load_rig(RIGS_DIR / f"{name}.toml")
            assert "break_in" in rig.capabilities, name
            assert rig.break_in_modes is None, name


class TestNotchWidthDomain:
    """'notch' is a broader capability than manual notch WIDTH — a radio
    can have auto-notch with no manual-notch-width command at all (IC-705
    has no CI-V 0x16 0x57 mapping yet still declares width_values "for UI
    parity"; X6100/X6200/FTX-1/TX-500 have neither the mapping nor a width
    domain). So, unlike ssb_tx_bw/filter_shape below, this is NOT a strict
    capability-implies-domain invariant — just direct regression pins.

    MOR-1551 audited the four profiles below (none had a ``[notch]
    width_values`` domain) against in-repo sources and found no trustworthy
    evidence of an actual manual-notch WIDTH control on any of them — see
    the per-profile ``[capabilities]`` comments in each ``.toml`` for the
    full provenance. This class pins that "undeclared" is the correct,
    audited state, not an oversight."""

    def test_ic7610_family_declares_wide_mid_nar(self):
        for name in ("ic705", "ic7300", "ic9700", "ic7610"):
            rig = load_rig(RIGS_DIR / f"{name}.toml")
            assert rig.notch_width_values == (0, 1, 2), name
            assert rig.notch_width_labels == {
                "0": "WIDE",
                "1": "MID",
                "2": "NAR",
            }, name

    def test_x6100_x6200_declare_notch_without_a_width_domain(self):
        """X6200's own cat-audit (docs/validation/cat-audits/x6200.md lines
        55/95/107) found no WIDE/MID/NAR-style width register anywhere in
        the documented CI-V table — its real notch is a DNF toggle
        (0x16 0x41) + center frequency (0x14 0x0D). X6100 has no hardware
        to audit directly and leans on X6100/X6200 shared-firmware
        inference (same basis as the existing break_in exception)."""
        for name in ("x6100", "x6200"):
            rig = load_rig(RIGS_DIR / f"{name}.toml")
            assert "notch" in rig.capabilities, name
            assert rig.notch_width_values is None, name

    def test_ftx1_declares_notch_without_a_width_domain(self):
        """FTX-1's manual notch is a Yaesu CAT BP00 (on/off) + BP01
        (frequency index 0-255) pair — no separate WIDTH register is
        documented in wfview's FTX-1.rig or the Yaesu FT-X1 CAT manual.
        YaesuCatRadio.set_manual_notch_width/get_manual_notch_width also
        raise NotImplementedError unconditionally, so this control is not
        wire-reachable on this backend regardless of the TOML domain."""
        rig = load_rig(RIGS_DIR / "ftx1.toml")
        assert "notch" in rig.capabilities
        assert rig.notch_width_values is None

    def test_tx500_declares_notch_without_a_width_domain(self):
        """TX-500's NT command is a single 0=OFF/1=Auto toggle per the
        Lab599 CAT Protocol rev. 2 (docs/validation/cat-audits/tx500.md
        line 37) — auto-notch only, no manual notch and therefore no width
        parameter documented anywhere in the protocol doc."""
        rig = load_rig(RIGS_DIR / "tx500.toml")
        assert "notch" in rig.capabilities
        assert rig.notch_width_values is None


class TestSsbTxBwDomainDeclaredOrCapabilityAbsent:
    @pytest.mark.parametrize("toml_path", _SHIPPED_RIG_TOMLS, ids=lambda p: p.stem)
    def test_ssb_tx_bw_capability_and_domain_agree(self, toml_path):
        rig = load_rig(toml_path)
        has_capability = "ssb_tx_bw" in rig.capabilities

        if not has_capability:
            assert rig.ssb_tx_bw_values is None, (
                f"{toml_path.name}: declares [ssb_tx_bw] values without "
                f"the 'ssb_tx_bw' capability feature"
            )
            return

        assert rig.ssb_tx_bw_values, (
            f"{toml_path.name}: declares the 'ssb_tx_bw' capability but no "
            f"(or an empty) [ssb_tx_bw] values domain"
        )

    def test_ic7610_family_declares_wide_mid_nar(self):
        for name in ("ic705", "ic7300", "ic9700", "ic7610"):
            rig = load_rig(RIGS_DIR / f"{name}.toml")
            assert rig.ssb_tx_bw_values == (0, 1, 2), name


class TestFilterShapeDomainDeclaredOrCapabilityAbsent:
    """MOR-1534: filter_shape had NO TOML domain at all before this ticket
    (unlike the other three, which were declared-but-dead). Added here for
    the exact four profiles that already declare the ``filter_shape``
    capability, sourced from docs/validation/cat-audits/{ic7300,ic7610}.md
    (S/R-confirmed for those two) and, for ic705/ic9700 (no dedicated
    cat-audit in-repo), from the shared runtime ``FilterShape`` enum's
    pre-existing SHARP=0/SOFT=1 assumption for the same CI-V 0x16 0x56
    command — see each profile's ``[filter_shape]`` TOML comment for the
    exact provenance."""

    @pytest.mark.parametrize("toml_path", _SHIPPED_RIG_TOMLS, ids=lambda p: p.stem)
    def test_filter_shape_capability_and_domain_agree(self, toml_path):
        rig = load_rig(toml_path)
        has_capability = "filter_shape" in rig.capabilities

        if not has_capability:
            assert rig.filter_shape_values is None, (
                f"{toml_path.name}: declares [filter_shape] values without "
                f"the 'filter_shape' capability feature"
            )
            return

        assert rig.filter_shape_values, (
            f"{toml_path.name}: declares the 'filter_shape' capability but "
            f"no (or an empty) [filter_shape] values domain"
        )

    def test_ic7610_family_declares_sharp_soft(self):
        for name in ("ic705", "ic7300", "ic9700", "ic7610"):
            rig = load_rig(RIGS_DIR / f"{name}.toml")
            assert rig.filter_shape_values == (0, 1), name
            assert rig.filter_shape_labels == {"0": "SHARP", "1": "SOFT"}, name
