"""Tests for CommandSpec (CI-V + CAT command specifications)."""

from __future__ import annotations

import copy
import dataclasses
import json
import textwrap
from pathlib import Path

import pytest

from rigplane.command_spec import AbsentCommandSpec, CatCommandSpec, CivCommandSpec
from rigplane.rig_loader import RigLoadError, load_rig
from test_rig_loader import TestIc7610DeclaresAbsentCommands as _Ic7610AbsentPin
from test_rig_loader import TestTx500DeclaresAbsentCommands as _Tx500AbsentPin
from test_rig_loader import TestX6100DeclaresAbsentCommands as _X6100AbsentPin
from test_rig_loader import TestX6200DeclaresAbsentCommands as _X6200AbsentPin


def _write_toml(tmp_path: Path, content: str, name: str = "test.toml") -> Path:
    """Write a TOML string to a temp file and return the path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return p


_MINIMAL_CIV_TOML = """\
[radio]
id = "icom_ic7300"
model = "IC-7300"
civ_addr = 0x94
receiver_count = 1
has_lan = true
has_wifi = false

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
get_mode = [0x04]
set_mode = [0x06]
"""


_MINIMAL_CAT_TOML = """\
[radio]
id = "yaesu_ftx1"
model = "FTX-1"
receiver_count = 2
has_lan = false
has_wifi = false
default_baud = 38400

[protocol]
type = "yaesu_cat"

[capabilities]
features = ["audio", "dual_rx", "meters", "tx"]

[modes]
list = ["USB", "LSB", "CW-U", "FM"]

[filters]
list = ["FIL1", "FIL2"]

[vfo]
scheme = "ab_shared"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 30000
end_hz = 60000000

[commands]
get_freq = { cat = { read = "FA;", parse = "FA{freq:09d};" } }
set_freq = { cat = { write = "FA{freq:09d};" } }
get_mode = { cat = { read = "MD0;", parse = "MD0{mode};" } }
set_mode = { cat = { write = "MD0{mode};" } }
"""


_MIXED_COMMANDS_TOML = """\
[radio]
id = "mixed_test"
model = "Mixed Test"
receiver_count = 1
has_lan = false
has_wifi = false

[capabilities]
features = ["meters"]

[modes]
list = ["USB"]

[filters]
list = ["FIL1"]

[vfo]
scheme = "single"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 30000
end_hz = 60000000

[commands]
# CI-V style
get_freq = [0x03]
# CAT style
get_mode = { cat = { read = "MD0;" } }
"""


class TestCivCommandSpec:
    """Tests for CI-V command specifications (existing format)."""

    def test_legacy_constructor_defaults_to_no_value_variants(self):
        spec = CivCommandSpec(bytes=(0x03,))

        assert spec.value_variants == ()
        projection = dataclasses.asdict(spec)
        assert projection == {"bytes": (0x03,), "value_variants": ()}
        assert json.loads(json.dumps(projection)) == {
            "bytes": [0x03],
            "value_variants": [],
        }
        assert copy.deepcopy(spec) == spec

    def test_populated_value_variants_have_json_safe_detached_projection(self):
        variants = {
            0: [0x1A, 0x06, 0x00, 0x00],
            1: [0x1A, 0x06, 0x01, 0x01],
        }
        spec = CivCommandSpec(bytes=(0x1A, 0x06), value_variants=variants)

        variants[0][3] = 0xFF
        variants[2] = [0x1A, 0x06, 0x02, 0x02]

        expected = (
            (0, (0x1A, 0x06, 0x00, 0x00)),
            (1, (0x1A, 0x06, 0x01, 0x01)),
        )
        assert spec.value_variants == expected
        projection = dataclasses.asdict(spec)
        assert projection == {
            "bytes": (0x1A, 0x06),
            "value_variants": expected,
        }
        assert json.loads(json.dumps(projection)) == {
            "bytes": [0x1A, 0x06],
            "value_variants": [
                [0, [0x1A, 0x06, 0x00, 0x00]],
                [1, [0x1A, 0x06, 0x01, 0x01]],
            ],
        }
        assert copy.deepcopy(spec) == spec
        assert hash(copy.deepcopy(spec)) == hash(spec)
        reordered = CivCommandSpec(
            bytes=(0x1A, 0x06),
            value_variants={
                1: [0x1A, 0x06, 0x01, 0x01],
                0: [0x1A, 0x06, 0x00, 0x00],
            },
        )
        assert reordered == spec
        assert hash(reordered) == hash(spec)

    def test_load_civ_commands(self, tmp_path):
        """CI-V commands load as CivCommandSpec."""
        p = _write_toml(tmp_path, _MINIMAL_CIV_TOML)
        rig = load_rig(p)

        assert "get_freq" in rig.commands
        assert isinstance(rig.commands["get_freq"], CivCommandSpec)
        assert rig.commands["get_freq"].bytes == (0x03,)

        assert "set_freq" in rig.commands
        assert isinstance(rig.commands["set_freq"], CivCommandSpec)
        assert rig.commands["set_freq"].bytes == (0x05,)

    def test_civ_multi_byte_command(self, tmp_path):
        """Multi-byte CI-V commands parse correctly."""
        toml = _MINIMAL_CIV_TOML + "\nget_rf_gain = [0x14, 0x02]\n"
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)

        assert isinstance(rig.commands["get_rf_gain"], CivCommandSpec)
        assert rig.commands["get_rf_gain"].bytes == (0x14, 0x02)

    def test_civ_empty_list_rejected(self, tmp_path):
        """Empty CI-V byte list is rejected."""
        toml = _MINIMAL_CIV_TOML + "\nget_invalid = []\n"
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="empty list not allowed"):
            load_rig(p)

    def test_civ_invalid_byte_value_rejected(self, tmp_path):
        """CI-V byte values outside 0x00–0xFF are rejected."""
        toml = _MINIMAL_CIV_TOML + "\nget_invalid = [0x03, 0x100]\n"
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="0x00–0xFF"):
            load_rig(p)

    def test_civ_non_integer_rejected(self, tmp_path):
        """CI-V byte list with non-integers is rejected."""
        toml = _MINIMAL_CIV_TOML + '\nget_invalid = [0x03, "bad"]\n'
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="must be all integers"):
            load_rig(p)

    def test_value_variants_load_as_one_immutable_civ_spec(self, tmp_path):
        toml = (
            _MINIMAL_CIV_TOML
            + '\nset_data_mode = { bytes = [0x1A, 0x06], value_variants = { "0" = [0x1A, 0x06, 0x00, 0x00], "1" = [0x1A, 0x06, 0x01, 0x01] } }\n'
        )
        rig = load_rig(_write_toml(tmp_path, toml, "variants.toml"))

        spec = rig.commands["set_data_mode"]
        assert isinstance(spec, CivCommandSpec)
        assert spec.bytes == (0x1A, 0x06)
        assert spec.value_variants == (
            (0, (0x1A, 0x06, 0x00, 0x00)),
            (1, (0x1A, 0x06, 0x01, 0x01)),
        )
        with pytest.raises(TypeError):
            spec.value_variants[0] = (0, (0x1A, 0x06, 0x00))  # type: ignore[index]
        assert copy.deepcopy(spec.value_variants) == spec.value_variants

    @pytest.mark.parametrize(
        ("declaration", "path"),
        [
            (
                "set_data_mode = { bytes = [0x1A, 0x06], value_variants = {} }",
                r"variants\.toml: \[commands\]\.set_data_mode\.value_variants",
            ),
            (
                'set_data_mode = { bytes = [], value_variants = { "0" = [0x1A, 0x06, 0x00] } }',
                r"variants\.toml: \[commands\]\.set_data_mode\.bytes",
            ),
            (
                'set_data_mode = { bytes = [0x1A, 0x06], value_variants = { "01" = [0x1A, 0x06, 0x01] } }',
                r"variants\.toml: \[commands\]\.set_data_mode\.value_variants\.01",
            ),
            (
                'set_data_mode = { bytes = [0x1A, 0x06], value_variants = { "0" = [0x1A, 0x06, true] } }',
                r"variants\.toml: \[commands\]\.set_data_mode\.value_variants\.0",
            ),
            (
                'set_data_mode = { bytes = [0x1A, 0x06], value_variants = { "0" = [0x1A, 0x06, 0x100] } }',
                r"variants\.toml: \[commands\]\.set_data_mode\.value_variants\.0",
            ),
            (
                'set_data_mode = { bytes = [0x1A, 0x06], value_variants = { "0" = [0x1A, 0x06] } }',
                r"variants\.toml: \[commands\]\.set_data_mode\.value_variants\.0",
            ),
            (
                'set_data_mode = { bytes = [0x1A, 0x06], value_variants = { "0" = [0x1A, 0x07, 0x00] } }',
                r"variants\.toml: \[commands\]\.set_data_mode\.value_variants\.0",
            ),
            (
                'set_data_mode = { bytes = [0x1A, 0x06], value_variants = { "0" = [0x1A, 0x06, 0x00], "1" = [0x1A, 0x06, 0x00] } }',
                r"variants\.toml: \[commands\]\.set_data_mode\.value_variants\.1",
            ),
            (
                'set_data_mode = { bytes = [0x1A, 0x06], value_variants = { "0" = [0x1A, 0x06, 0x00] }, extra = true }',
                r"variants\.toml: \[commands\]\.set_data_mode",
            ),
            (
                "set_data_mode = { bytes = [0x1A, 0x06] }",
                r"variants\.toml: \[commands\]\.set_data_mode",
            ),
            (
                'set_data_mode = { cat = { write = "DM{mode};" }, bytes = [0x1A, 0x06] }',
                r"variants\.toml: \[commands\]\.set_data_mode",
            ),
            (
                'set_data_mode = { value_variants = { "0" = [0x1A, 0x06, 0x00] } }',
                r"variants\.toml: \[commands\]\.set_data_mode",
            ),
        ],
    )
    def test_invalid_value_variant_shapes_name_the_exact_profile_path(
        self, tmp_path: Path, declaration: str, path: str
    ) -> None:
        toml = _MINIMAL_CIV_TOML + "\n" + declaration + "\n"

        with pytest.raises(RigLoadError, match=path):
            load_rig(_write_toml(tmp_path, toml, "variants.toml"))


class TestCatCommandSpec:
    """Tests for Yaesu CAT command specifications (new format)."""

    def test_load_cat_commands(self, tmp_path):
        """CAT commands load as CatCommandSpec."""
        p = _write_toml(tmp_path, _MINIMAL_CAT_TOML)
        rig = load_rig(p)

        assert "get_freq" in rig.commands
        spec = rig.commands["get_freq"]
        assert isinstance(spec, CatCommandSpec)
        assert spec.read == "FA;"
        assert spec.parse == "FA{freq:09d};"
        assert spec.write is None

    def test_cat_write_only_command(self, tmp_path):
        """CAT write-only command (no read)."""
        p = _write_toml(tmp_path, _MINIMAL_CAT_TOML)
        rig = load_rig(p)

        spec = rig.commands["set_freq"]
        assert isinstance(spec, CatCommandSpec)
        assert spec.write == "FA{freq:09d};"
        assert spec.read is None
        assert spec.parse is None

    def test_cat_read_write_command(self, tmp_path):
        """CAT command with both read and write."""
        toml = (
            _MINIMAL_CAT_TOML
            + """
get_ptt = { cat = { read = "TX;", write = "TX{state};", parse = "TX{state};" } }
"""
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)

        spec = rig.commands["get_ptt"]
        assert isinstance(spec, CatCommandSpec)
        assert spec.read == "TX;"
        assert spec.write == "TX{state};"
        assert spec.parse == "TX{state};"

    def test_cat_missing_both_read_write_rejected(self, tmp_path):
        """CAT command without read or write is rejected."""
        toml = (
            _MINIMAL_CAT_TOML
            + """
get_invalid = { cat = { parse = "FA{freq:09d};" } }
"""
        )
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="at least one of 'read' or 'write'"):
            load_rig(p)

    def test_cat_dict_without_cat_key_rejected(self, tmp_path):
        """Command dict without 'cat' key is rejected."""
        toml = (
            _MINIMAL_CAT_TOML
            + """
get_invalid = { read = "FA;" }
"""
        )
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="must have 'cat' key"):
            load_rig(p)

    def test_cat_non_string_values_rejected(self, tmp_path):
        """CAT command with non-string values is rejected."""
        toml = (
            _MINIMAL_CAT_TOML
            + """
get_invalid = { cat = { read = 123 } }
"""
        )
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="read must be a string"):
            load_rig(p)


class TestMixedCommands:
    """Tests for rigs with both CI-V and CAT commands."""

    def test_mixed_commands_load(self, tmp_path):
        """Rig with both CI-V and CAT commands loads correctly."""
        p = _write_toml(tmp_path, _MIXED_COMMANDS_TOML)
        rig = load_rig(p)

        # CI-V command
        assert isinstance(rig.commands["get_freq"], CivCommandSpec)
        assert rig.commands["get_freq"].bytes == (0x03,)

        # CAT command
        assert isinstance(rig.commands["get_mode"], CatCommandSpec)
        assert rig.commands["get_mode"].read == "MD0;"

    def test_command_map_filters_civ_only(self, tmp_path):
        """CommandMap only includes CI-V commands, not CAT."""
        p = _write_toml(tmp_path, _MIXED_COMMANDS_TOML)
        rig = load_rig(p)

        cmd_map = rig.to_command_map()

        # CI-V command is included
        assert cmd_map.has("get_freq")
        assert cmd_map.get("get_freq") == (0x03,)

        # CAT command is NOT included
        assert not cmd_map.has("get_mode")


class TestAbsentCommandSpec:
    """Tests for the declared-absent command spelling (MOR-2005 step 4a).

    ``{ absent = "<source>" }`` records that a named authority (a manual, a
    wfview rig definition, ...) confirms this radio does not have the
    command — a D2-compliant source, not just a gap. Step 4b (a later PR)
    adds the refusal policy that consumes this; here the shape only needs
    to parse and round-trip.
    """

    def test_absent_command_round_trips(self, tmp_path):
        """The absent spelling loads as AbsentCommandSpec with its source."""
        toml = (
            _MINIMAL_CIV_TOML
            + '\nget_dual_watch = { absent = "IC-7300 Full Manual (A7292-4EX), no dual-watch item" }\n'
        )
        p = _write_toml(tmp_path, toml)
        rig = load_rig(p)

        assert "get_dual_watch" in rig.commands
        spec = rig.commands["get_dual_watch"]
        assert isinstance(spec, AbsentCommandSpec)
        assert spec.source == "IC-7300 Full Manual (A7292-4EX), no dual-watch item"

    def test_absent_source_empty_string_rejected(self, tmp_path):
        """An empty source defeats D2's provenance requirement."""
        toml = _MINIMAL_CIV_TOML + '\nget_dual_watch = { absent = "" }\n'
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="non-empty"):
            load_rig(p)

    def test_absent_and_cat_together_rejected(self, tmp_path):
        """'absent' and 'cat' are mutually exclusive markers in one entry."""
        toml = (
            _MINIMAL_CIV_TOML
            + '\nget_dual_watch = { absent = "some manual", cat = { read = "DW;" } }\n'
        )
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="extra"):
            load_rig(p)

    def test_absent_with_unknown_extra_key_rejected(self, tmp_path):
        """No other keys are allowed alongside 'absent'."""
        toml = (
            _MINIMAL_CIV_TOML
            + '\nget_dual_watch = { absent = "some manual", note = "x" }\n'
        )
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="extra"):
            load_rig(p)

    def test_dict_without_cat_or_absent_key_still_rejected(self, tmp_path):
        """A dict with neither marker key keeps its original message —
        this pins that the new 'absent' branch does not swallow the
        pre-existing 'cat'-key error (unchanged since before MOR-2005)."""
        toml = _MINIMAL_CIV_TOML + '\nget_invalid = { read = "FA;" }\n'
        p = _write_toml(tmp_path, toml)

        with pytest.raises(RigLoadError, match="must have 'cat' key"):
            load_rig(p)


class TestBackwardCompatibility:
    """Ensure existing CI-V rigs load unchanged."""

    def test_ic7610_loads_civ_except_the_declared_absent_tone_tsql_family(self):
        """IC-7610 rig loads with CI-V commands as before -- except the
        eight repeater-tone/TSQL/tone-freq/TSQL-freq keys, which MOR-2008
        batch 2 promoted from a comment-only exclusion to a formal
        ``{ absent = "<source>" }`` row (``AbsentCommandSpec``, not
        ``CivCommandSpec``): see ``rigs/ic7610.toml``'s own rows for the
        citation. Renamed from ``test_ic7610_loads_unchanged``, which this
        promotion made false -- that version asserted every single command
        was ``CivCommandSpec`` with no exception.

        The expected-absent set is imported from
        ``test_rig_loader.py: TestIc7610DeclaresAbsentCommands`` rather
        than duplicated here, so this file adds no fourth copy of the
        eight names
        (``test_rig_loader.py: test_ic7610_drops_dead_tone_commands`` and
        ``test_rig_ic7610.py: test_no_repeater_tone_family`` enumerate
        them too, but pin a different property -- absence from the
        command map, not the parsed spec type); this test then checks a
        different layer against that same external pin (the raw
        ``rig.commands`` dict this file's own ``CommandSpec`` types come
        from, rather than the derived ``RadioProfile.absent_command_names``
        property `test_rig_loader.py` checks) -- not merely re-deriving
        one from the other, which would never catch a break in the
        conversion between them. Still discriminating either way: a CI-V
        row silently becoming absent, or an absent row silently reverting
        to CI-V, fails this test regardless of which of the two names
        moves.
        """
        rigs_dir = Path(__file__).resolve().parent.parent / "rigs"
        p = rigs_dir / "ic7610.toml"

        if not p.exists():
            pytest.skip("ic7610.toml not found")

        rig = load_rig(p)

        expected_absent = _Ic7610AbsentPin._EXPECTED_ABSENT
        for name, spec in rig.commands.items():
            if name in expected_absent:
                assert isinstance(spec, AbsentCommandSpec), (
                    f"Command {name} is declared absent but not AbsentCommandSpec"
                )
            else:
                assert isinstance(spec, CivCommandSpec), (
                    f"Command {name} is not CivCommandSpec"
                )
        actually_absent = {
            name
            for name, spec in rig.commands.items()
            if isinstance(spec, AbsentCommandSpec)
        }
        assert actually_absent == expected_absent, (
            "rig.commands' AbsentCommandSpec entries drifted from "
            "TestIc7610DeclaresAbsentCommands._EXPECTED_ABSENT"
        )

        # CommandMap should work as before
        cmd_map = rig.to_command_map()
        assert cmd_map.has("get_freq")
        assert cmd_map.has("set_freq")

    def _assert_loads_except_declared_absent(
        self, toml_name: str, expected_absent: frozenset[str], live_spec_type: type
    ) -> None:
        """Shared body for the four profile checks below: every
        command in ``rig.commands`` is *live_spec_type* except the pinned
        absent set, discriminating both directions the same way
        ``test_ic7610_loads_civ_except_the_declared_absent_tone_tsql_family``
        above does -- a row silently becoming absent, or an absent row
        silently reverting to live, fails regardless of which name moves.
        X6200/X6100 retain CI-V specs; FTX-1/TX-500 retain CAT specs.
        FTX-1's manual-only policy requires an empty absent set.
        """
        rigs_dir = Path(__file__).resolve().parent.parent / "rigs"
        p = rigs_dir / toml_name

        if not p.exists():
            pytest.skip(f"{toml_name} not found")

        rig = load_rig(p)

        for name, spec in rig.commands.items():
            if name in expected_absent:
                assert isinstance(spec, AbsentCommandSpec), (
                    f"Command {name} is declared absent but not AbsentCommandSpec"
                )
            else:
                assert isinstance(spec, live_spec_type), (
                    f"Command {name} is not {live_spec_type.__name__}"
                )
        actually_absent = {
            name
            for name, spec in rig.commands.items()
            if isinstance(spec, AbsentCommandSpec)
        }
        assert actually_absent == expected_absent, (
            f"{toml_name}'s AbsentCommandSpec entries drifted from its expected set"
        )

    def test_x6200_loads_civ_except_the_declared_absent_group_b_family(self) -> None:
        """MOR-2008 batch 4: X6200 declares 17 keys absent (6 DSP-family
        from batch 3, 11 Group B memory/tx_band keys added here) --
        everything else in its ``[commands]`` table is CivCommandSpec, the
        5 Group B freq.py keys included (DECLARED-OK, not absent).
        """
        self._assert_loads_except_declared_absent(
            "x6200.toml", _X6200AbsentPin._EXPECTED_ABSENT, CivCommandSpec
        )

    def test_x6100_loads_civ_except_the_declared_absent_group_b_family(self) -> None:
        """Sibling of the X6200 check above (D2 MOR-2018 sibling-copy rule)."""
        self._assert_loads_except_declared_absent(
            "x6100.toml", _X6100AbsentPin._EXPECTED_ABSENT, CivCommandSpec
        )

    def test_ftx1_loads_only_cat_specs_without_absent_markers(self) -> None:
        """MOR-2257: every retained FTX-1 declaration is a CAT spec."""
        self._assert_loads_except_declared_absent(
            "ftx1.toml", frozenset(), CatCommandSpec
        )

    def test_tx500_loads_cat_except_the_declared_absent_group_b_family(self) -> None:
        """TX-500 retains its protocol-mismatch absent markers."""
        self._assert_loads_except_declared_absent(
            "tx500.toml", _Tx500AbsentPin._EXPECTED_ABSENT, CatCommandSpec
        )
