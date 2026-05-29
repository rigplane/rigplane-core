"""Unit tests for the pure override-merge layer (MOR-206, ADR §4)."""

from __future__ import annotations

import pytest

from rigplane.validation import (
    MergeReport,
    OverrideEntry,
    OverridePatch,
    merge_overrides,
    parse_override_dict,
)
from rigplane.validation.registry import build_template_from_capabilities
from rigplane.validation.schema import (
    CapabilityDeclaration,
    ValidationLevel,
    validate_template_dict,
)

# A representative capability set covering scope (manual), tx and tuner
# (tx-adjacent / blocked) so the safety invariant is exercised.
_CAPS = frozenset({"scope", "tx", "tuner", "rf_gain", "audio"})


def _generated() -> object:
    return build_template_from_capabilities(_CAPS, model="X", profile_id="x")


def _by_id(template: object) -> dict[str, object]:
    return {entry.check_id: entry for entry in template.entries}  # type: ignore[attr-defined]


def test_empty_patch_returns_equivalent_template() -> None:
    generated = _generated()
    patch = OverridePatch(profile_id="x", entries=())

    merged, report = merge_overrides(generated, patch)

    assert isinstance(report, MergeReport)
    assert [e.check_id for e in merged.entries] == [
        e.check_id for e in generated.entries
    ]
    assert {e.check_id: e.declaration for e in merged.entries} == {
        e.check_id: e.declaration for e in generated.entries
    }
    assert report.applied == ()
    assert report.appended == ()
    assert report.excluded == ()
    assert report.rejected == ()


def test_replace_field_updates_only_targeted_entry() -> None:
    generated = _generated()
    before = _by_id(generated)
    assert before["scope.capture"].declaration == CapabilityDeclaration.MANUAL_REQUIRED

    patch = OverridePatch(
        profile_id="x",
        entries=(
            OverrideEntry(
                check_id="scope.capture",
                declaration=CapabilityDeclaration.SUPPORTED.value,
                summary="Automated scope capture is safe here.",
            ),
        ),
    )

    merged, report = merge_overrides(generated, patch)
    after = _by_id(merged)

    assert after["scope.capture"].declaration == CapabilityDeclaration.SUPPORTED
    assert after["scope.capture"].summary == "Automated scope capture is safe here."
    assert "scope.capture" in report.applied
    # Every other entry is unchanged.
    for check_id, entry in before.items():
        if check_id == "scope.capture":
            continue
        assert after[check_id].declaration == entry.declaration
        assert after[check_id].summary == entry.summary


def test_append_adds_new_entry_and_template_validates() -> None:
    generated = _generated()
    patch = OverridePatch(
        profile_id="x",
        entries=(
            OverrideEntry(
                check_id="custom.extra",
                declaration=CapabilityDeclaration.SUPPORTED.value,
                capability="",
                summary="A custom extra check.",
            ),
        ),
    )

    merged, report = merge_overrides(generated, patch)
    after = _by_id(merged)

    assert "custom.extra" in after
    assert after["custom.extra"].declaration == CapabilityDeclaration.SUPPORTED
    assert "custom.extra" in report.appended
    # Round-trips through the schema validator.
    validate_template_dict(merged.to_dict())


def test_exclude_drops_entry_and_template_validates() -> None:
    generated = _generated()
    assert "rf_gain.set" in _by_id(generated)

    patch = OverridePatch(
        profile_id="x",
        entries=(OverrideEntry(check_id="rf_gain.set", declaration="excluded"),),
    )

    merged, report = merge_overrides(generated, patch)
    after = _by_id(merged)

    assert "rf_gain.set" not in after
    assert "rf_gain.set" in report.excluded
    validate_template_dict(merged.to_dict())


@pytest.mark.parametrize("check_id", ["tx.ptt", "tuner.tune"])
def test_safety_invariant_refuses_to_relax_tx_adjacent(check_id: str) -> None:
    generated = _generated()
    patch = OverridePatch(
        profile_id="x",
        entries=(
            OverrideEntry(
                check_id=check_id,
                tx_adjacent=False,
                declaration=CapabilityDeclaration.SUPPORTED.value,
                summary="Operator says this is fine (it is not).",
            ),
        ),
    )

    merged, report = merge_overrides(generated, patch)
    after = _by_id(merged)

    # tx_adjacent stays True; declaration is NOT auto-actuating (not SUPPORTED).
    assert after[check_id].tx_adjacent is True
    assert after[check_id].declaration != CapabilityDeclaration.SUPPORTED
    assert check_id in report.rejected
    # Safe fields (summary) still applied.
    assert after[check_id].summary == "Operator says this is fine (it is not)."


def test_parse_override_dict_round_trip() -> None:
    data = {
        "schema_version": 1,
        "radio": {"model": "IC-7300", "profile_id": "ic7300"},
        "override": True,
        "entries": [
            {
                "check_id": "scope.capture",
                "capability": "scope",
                "level": 4,
                "declaration": "supported",
                "summary": "Automated scope capture is safe on IC-7300.",
                "tx_adjacent": False,
            },
            {"check_id": "rf_gain.set", "declaration": "excluded"},
        ],
    }

    patch = parse_override_dict(data)

    assert isinstance(patch, OverridePatch)
    assert patch.profile_id == "ic7300"
    assert [e.check_id for e in patch.entries] == ["scope.capture", "rf_gain.set"]
    first = patch.entries[0]
    assert first.declaration == "supported"
    assert first.level == 4
    assert first.capability == "scope"
    assert first.tx_adjacent is False
    assert patch.entries[1].declaration == "excluded"


def test_parse_override_dict_missing_check_id_raises() -> None:
    data = {
        "schema_version": 1,
        "radio": {"model": "X", "profile_id": "x"},
        "entries": [{"declaration": "supported"}],
    }
    with pytest.raises(ValueError):
        parse_override_dict(data)


def test_parse_override_dict_missing_radio_raises() -> None:
    with pytest.raises(ValueError):
        parse_override_dict({"schema_version": 1, "entries": []})


def test_merge_is_deterministic_and_sorted_by_level() -> None:
    generated = _generated()
    patch = OverridePatch(
        profile_id="x",
        entries=(
            OverrideEntry(
                check_id="scope.capture",
                declaration=CapabilityDeclaration.SUPPORTED.value,
            ),
            OverrideEntry(
                check_id="custom.extra",
                declaration=CapabilityDeclaration.SUPPORTED.value,
                level=int(ValidationLevel.BASIC_CONTROL),
            ),
        ),
    )

    merged_a, _ = merge_overrides(generated, patch)
    merged_b, _ = merge_overrides(generated, patch)

    # Sorted by level ascending.
    levels = [int(e.level) for e in merged_a.entries]
    assert levels == sorted(levels)
    # Applying twice yields identical serialized output.
    assert merged_a.to_dict() == merged_b.to_dict()
