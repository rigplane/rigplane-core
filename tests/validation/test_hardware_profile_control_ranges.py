"""MOR-2476: profile bands drive filter/if-shift/nb-nr validation steps.

filter_width.set branches on ``filter_width_encoding``, if_shift.set nudges
inside ``[controls.if_shift]``, and the nb/nr level fallback probes an "on"
level inside the declared band (SKIP when none).
"""

from __future__ import annotations

from types import SimpleNamespace

from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


def _single_entry_template(*, check_id: str, capability: str) -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="FAKE", profile_id="fake"),
        entries=[
            CapabilityDeclarationEntry(
                check_id=check_id,
                capability=capability,
                level=ValidationLevel.CAPABILITY_MATRIX,
                declaration=CapabilityDeclaration.SUPPORTED,
                summary="single",
            )
        ],
    )


async def _run(radio, *, check_id: str, capability: str):
    template = _single_entry_template(check_id=check_id, capability=capability)
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    return _flatten(levels)[check_id]


class _FakeRadio:
    """Stateful fake: named get/set op pairs, OOB writes ignored, profile set."""

    def __init__(self, *, capabilities, controls=None, encoding="segmented_bcd_index"):
        self.connected = True
        self.model = "FAKE"
        self.capabilities = set(capabilities)
        self.profile = SimpleNamespace(
            controls=controls, filter_width_encoding=encoding
        )
        self.writes = []

    def add_op(self, get_op, set_op, *, start, band):
        """Add a get/set pair round-tripping ``start`` within ``band``."""
        store = {"value": start}

        async def _get(*args):
            return store["value"]

        async def _set(value, *args):
            self.writes.append(value)
            if band[0] <= value <= band[1]:
                store["value"] = value

        setattr(self, get_op, _get)
        setattr(self, set_op, _set)
        return self


async def test_if_shift_nudge_comes_from_profile_band():
    """if_shift +/-100 Hz profile: nudge to 20, every write inside the band."""
    radio = _FakeRadio(
        capabilities={"if_shift"},
        controls={"if_shift": {"display_min": -100, "display_max": 100}},
    ).add_op("get_if_shift", "set_if_shift", start=0, band=(-100, 100))
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 20
    assert all(-100 <= w <= 100 for w in radio.writes)


async def test_if_shift_profile_without_band_skips():
    """Profiled radio with no if_shift band SKIPs; nothing is written."""
    radio = _FakeRadio(capabilities={"if_shift"}, controls={}).add_op("get_if_shift", "set_if_shift", start=0, band=(-1200, 1200))
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.SKIP
    assert "no declared range" in check.evidence["reason"]
    assert radio.writes == []


async def test_filter_table_index_encoding_selects_index_branch():
    """table_index profile: readback 35 is a code, nudged to 30 (not +200)."""
    radio = _FakeRadio(
        capabilities={"filter_width"}, encoding="table_index"
    ).add_op("get_filter_width", "set_filter_width", start=35, band=(0, 9999))
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 30
    assert check.evidence["readback"] == 30


async def test_filter_hz_encoding_selects_hz_branch():
    """Hz-encoding profile: readback 25 is Hz, nudged to 225 (not an index)."""
    radio = _FakeRadio(capabilities={"filter_width"}).add_op(
        "get_filter_width", "set_filter_width", start=25, band=(0, 9999)
    )
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 225
    assert check.evidence["readback"] == 225


async def test_nb_nr_on_level_comes_from_profile_band():
    """0-20 profile band: mid-band "on" probe 10 instead of hard-coded 5."""
    for name in ("nb", "nr"):
        radio = _FakeRadio(
            capabilities={name},
            controls={name: {"range_min": 0, "range_max": 20}},
        ).add_op(f"get_{name}_level", f"set_{name}_level", start=0, band=(0, 20))
        check = await _run(radio, check_id=f"{name}.set", capability=name)
        assert check.status is CheckStatus.PASS, name
        assert check.evidence["changed"] == 10, name
        assert all(0 <= w <= 20 for w in radio.writes), name


async def test_nb_level_profile_without_band_skips():
    """Profiled radio with no NB band SKIPs the level fallback; no writes."""
    radio = _FakeRadio(capabilities={"nb"}, controls={}).add_op("get_nb_level", "set_nb_level", start=0, band=(0, 10))
    check = await _run(radio, check_id="nb.set", capability="nb")
    assert check.status is CheckStatus.SKIP
    assert "no declared range" in check.evidence["reason"]
    assert radio.writes == []
