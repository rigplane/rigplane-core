"""Template tests: every shipped validation template parses and is valid."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rigplane.core.capabilities import KNOWN_CAPABILITIES
from rigplane.rig_loader import load_rig
from rigplane.validation import validate_template_dict

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES_DIR = _REPO_ROOT / "docs" / "validation" / "templates"
_RIGS_DIR = _REPO_ROOT / "rigs"


def _template_paths() -> list[Path]:
    return sorted(_TEMPLATES_DIR.glob("*.json"))


def test_templates_dir_has_templates() -> None:
    assert _template_paths(), "expected at least one validation template"


@pytest.mark.parametrize("path", _template_paths(), ids=lambda p: p.stem)
def test_template_parses_and_validates(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    # Sparse override patches (``"override": true``) are not full templates:
    # they carry partial entries (e.g. exclusion sentinels) and are parsed by
    # the override layer, not the template validator. Validate them as patches.
    if isinstance(data, dict) and data.get("override") is True:
        from rigplane.validation import parse_override_dict

        patch = parse_override_dict(data)
        for entry in patch.entries:
            if entry.capability:
                assert entry.capability in KNOWN_CAPABILITIES
        return
    template = validate_template_dict(data)
    for entry in template.entries:
        if entry.capability:
            assert entry.capability in KNOWN_CAPABILITIES


# ---------------------------------------------------------------------------
# FTX-1 agc/xit declaration (MOR-500)
#
# The live FTX-1 backend implements AGC (CAT ``GT0``) and XIT (clarifier
# ``CF000`` TX bit) and the radio reacts to both. The validation matrix only
# surfaces them as supported once the profile declares the ``agc``/``xit``
# capabilities AND the shipped template declares ``agc.set``/``xit.set`` as
# supported (rather than ``unsupported_pending_evidence``).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capability", ["agc", "xit"])
def test_ftx1_profile_declares_capability(capability: str) -> None:
    rig = load_rig(_RIGS_DIR / "ftx1.toml")
    assert capability in rig.capabilities


@pytest.mark.parametrize("check_id", ["agc.set", "xit.set"])
def test_ftx1_template_declares_supported(check_id: str) -> None:
    data = json.loads((_TEMPLATES_DIR / "ftx1.json").read_text(encoding="utf-8"))
    entry = next(e for e in data["entries"] if e["check_id"] == check_id)
    assert entry["declaration"] == "supported"
    assert entry["capability"] == check_id.split(".", 1)[0]
