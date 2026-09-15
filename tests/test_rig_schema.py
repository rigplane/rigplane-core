"""Tests for rig TOML schema validation.

Validates that _template.toml conforms to the rig schema specification
and that _schema.md documentation exists.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from rigplane.capabilities import KNOWN_CAPABILITIES

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
TEMPLATE_PATH = RIGS_DIR / "ic7610.toml"
SCHEMA_PATH = RIGS_DIR / "_schema.md"

REQUIRED_SECTIONS = [
    "radio",
    "spectrum",
    "capabilities",
    "freq_ranges",
    "modes",
    "filters",
    "vfo",
    "commands",
]

VALID_VFO_SCHEMES = {"ab", "main_sub"}


@pytest.fixture(scope="module")
def template() -> dict:
    """Load and parse the template TOML file."""
    text = TEMPLATE_PATH.read_bytes()
    return tomllib.loads(text.decode())


class TestTemplateParseable:
    """_template.toml must be valid TOML."""

    def test_toml_parseable(self):
        raw = TEMPLATE_PATH.read_bytes()
        data = tomllib.loads(raw.decode())
        assert isinstance(data, dict)


class TestRequiredSections:
    """All required top-level sections must be present."""

    def test_all_sections_present(self, template):
        for section in REQUIRED_SECTIONS:
            assert section in template, f"Missing required section: [{section}]"


class TestRadioSection:
    """[radio] section validation."""

    def test_has_id(self, template):
        assert isinstance(template["radio"]["id"], str)

    def test_has_model(self, template):
        assert isinstance(template["radio"]["model"], str)

    def test_has_civ_addr(self, template):
        addr = template["radio"]["civ_addr"]
        assert isinstance(addr, int)
        assert 0x00 <= addr <= 0xFF

    def test_has_receiver_count(self, template):
        count = template["radio"]["receiver_count"]
        assert isinstance(count, int)
        assert count >= 1

    def test_has_lan(self, template):
        assert isinstance(template["radio"]["has_lan"], bool)

    def test_has_wifi(self, template):
        assert isinstance(template["radio"]["has_wifi"], bool)


class TestSpectrumSection:
    """[spectrum] section validation."""

    def test_spectrum_values_are_ints(self, template):
        spec = template["spectrum"]
        for key in ("seq_max", "amp_max", "data_len_max"):
            assert isinstance(spec[key], int), f"spectrum.{key} must be int"
            assert spec[key] > 0, f"spectrum.{key} must be positive"


class TestCapabilities:
    """[capabilities] section validation."""

    def test_features_is_list(self, template):
        features = template["capabilities"]["features"]
        assert isinstance(features, list)

    def test_all_capabilities_known(self, template):
        features = template["capabilities"]["features"]
        for cap in features:
            assert cap in KNOWN_CAPABILITIES, (
                f"Unknown capability: {cap!r}. Known: {sorted(KNOWN_CAPABILITIES)}"
            )


class TestFreqRanges:
    """[[freq_ranges.ranges]] validation."""

    def test_ranges_is_list(self, template):
        ranges = template["freq_ranges"]["ranges"]
        assert isinstance(ranges, list)
        assert len(ranges) > 0

    def test_start_less_than_end(self, template):
        for r in template["freq_ranges"]["ranges"]:
            assert r["start_hz"] < r["end_hz"], (
                f"start_hz ({r['start_hz']}) must be < end_hz ({r['end_hz']})"
            )

    def test_has_label(self, template):
        for r in template["freq_ranges"]["ranges"]:
            assert isinstance(r["label"], str)
            assert len(r["label"]) > 0

    def test_bands_have_valid_freqs(self, template):
        for r in template["freq_ranges"]["ranges"]:
            if "bands" not in r:
                continue
            for band in r["bands"]:
                assert band["start_hz"] < band["end_hz"]
                assert isinstance(band["name"], str)
                assert isinstance(band["default_hz"], int)
                assert band["start_hz"] <= band["default_hz"] <= band["end_hz"]


class TestModes:
    """[modes] section validation."""

    def test_modes_list_nonempty(self, template):
        modes = template["modes"]["list"]
        assert isinstance(modes, list)
        assert len(modes) > 0

    def test_modes_are_strings(self, template):
        for m in template["modes"]["list"]:
            assert isinstance(m, str)


class TestFilters:
    """[filters] section validation."""

    def test_filters_list_nonempty(self, template):
        filters = template["filters"]["list"]
        assert isinstance(filters, list)
        assert len(filters) > 0


class TestVfoSection:
    """[vfo] section validation."""

    def test_scheme_is_valid(self, template):
        scheme = template["vfo"]["scheme"]
        assert scheme in VALID_VFO_SCHEMES, (
            f"vfo.scheme must be one of {VALID_VFO_SCHEMES}, got {scheme!r}"
        )

    def test_select_bytes_valid(self, template):
        vfo = template["vfo"]
        if "main_select" in vfo:
            _assert_wire_bytes(vfo["main_select"], "vfo.main_select")
        if "sub_select" in vfo:
            _assert_wire_bytes(vfo["sub_select"], "vfo.sub_select")
        if "swap" in vfo:
            _assert_wire_bytes(vfo["swap"], "vfo.swap")


class TestCommandsSection:
    """[commands] section validation — wire bytes are int arrays in 0x00–0xFF."""

    def test_commands_present(self, template):
        assert "commands" in template

    def test_wire_bytes_are_valid(self, template):
        cmds = template["commands"]
        for key, value in cmds.items():
            if key == "overrides":
                # overrides is a sub-table
                continue
            if isinstance(value, list):
                _assert_wire_bytes(value, f"commands.{key}")

    def test_overrides_wire_bytes_valid(self, template):
        overrides = template["commands"].get("overrides", {})
        for key, value in overrides.items():
            if isinstance(value, list):
                _assert_wire_bytes(value, f"commands.overrides.{key}")


class TestSchemaDoc:
    """_schema.md must exist and be non-empty."""

    def test_schema_md_exists(self):
        assert SCHEMA_PATH.exists(), f"{SCHEMA_PATH} does not exist"

    def test_schema_md_nonempty(self):
        content = SCHEMA_PATH.read_text()
        assert len(content.strip()) > 0


def _assert_wire_bytes(value: list, label: str) -> None:
    """Assert that value is a list of ints in 0x00–0xFF range."""
    assert isinstance(value, list), f"{label} must be a list, got {type(value)}"
    assert len(value) > 0, f"{label} must be non-empty"
    for i, b in enumerate(value):
        assert isinstance(b, int), f"{label}[{i}] must be int, got {type(b)}"
        assert 0x00 <= b <= 0xFF, f"{label}[{i}] = {b} out of range 0x00–0xFF"
