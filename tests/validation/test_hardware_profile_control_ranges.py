"""MOR-2476: hardware validation takes its control bands from the rig profile.

PR #3487 left hard-coded control ranges in
``rigplane.validation.hardware``: the filter-width index/Hz threshold (30),
the IF-shift band (+/-1200 Hz, 200 Hz nudge), and the NB/NR "on" probe level
(5). Each must come from the rig profile instead:

* ``filter_width.set`` branches on the profile's ``filter_width_encoding``
  (``table_index`` vs an Hz encoding), not on the readback value;
* ``if_shift.set`` nudges inside the ``[controls.if_shift]`` band;
* ``nb.set``/``nr.set`` level fallback probes an "on" level inside the
  ``nb``/``nr`` band from ``radio.profile.controls``.

A profiled radio declaring no band SKIPs honestly (no assumed band); a radio
with no profile at all keeps the owner-approved interim behaviour, pinned by
the pre-existing profile-less tests.
"""

from __future__ import annotations

import pytest

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


class _FakeProfile:
    """Minimal stand-in for ``RadioProfile``: controls + filter encoding."""

    def __init__(
        self,
        controls: dict[str, dict] | None,
        *,
        filter_width_encoding: str = "segmented_bcd_index",
    ) -> None:
        self.controls = controls
        self.filter_width_encoding = filter_width_encoding


class _ShiftRadio:
    """Stateful IF-shift fake; ignores out-of-band writes like a real radio."""

    def __init__(
        self,
        *,
        start: int,
        band: tuple[int, int],
        controls: dict[str, dict],
    ) -> None:
        self.connected = True
        self.model = "FAKE"
        self.capabilities = {"if_shift"}
        self.profile = _FakeProfile(controls)
        self._value = start
        self._band = band
        self.writes: list[int] = []

    async def get_if_shift(self, receiver: int = 0) -> int:
        return self._value

    async def set_if_shift(self, offset: int, receiver: int = 0) -> None:
        self.writes.append(offset)
        lo, hi = self._band
        if lo <= offset <= hi:
            self._value = offset


class _FilterRadio:
    """Stateful filter-width fake round-tripping any written value."""

    def __init__(self, *, start: int, encoding: str) -> None:
        self.connected = True
        self.model = "FAKE"
        self.capabilities = {"filter_width"}
        self.profile = _FakeProfile(None, filter_width_encoding=encoding)
        self._value = start
        self.writes: list[int] = []

    async def get_filter_width(self, receiver: int = 0) -> int:
        return self._value

    async def set_filter_width(self, value: int, receiver: int = 0) -> None:
        self.writes.append(value)
        self._value = value


class _NbNrLevelRadio:
    """Level-only NB/NR fake (no bool ops); ignores out-of-band writes."""

    def __init__(
        self,
        *,
        name: str,
        start: int,
        band: tuple[int, int],
        controls: dict[str, dict],
    ) -> None:
        self.connected = True
        self.model = "FAKE"
        self.capabilities = {name}
        self.profile = _FakeProfile(controls)
        self._value = start
        self._band = band
        self.writes: list[int] = []

        async def _get() -> int:
            return self._value

        async def _set(level: int) -> None:
            self.writes.append(level)
            lo, hi = self._band
            if lo <= level <= hi:
                self._value = level

        setattr(self, f"get_{name}_level", _get)
        setattr(self, f"set_{name}_level", _set)


# ---------------------------------------------------------------------------
# if_shift.set — band from [controls.if_shift]
# ---------------------------------------------------------------------------


async def test_if_shift_nudge_comes_from_profile_band():
    """A profile declaring if_shift +/-100 Hz drives the mutation: the nudge
    stays inside the declared band instead of the hard-coded +/-1200."""
    radio = _ShiftRadio(
        start=0,
        band=(-100, 100),
        controls={"if_shift": {"display_min": -100, "display_max": 100}},
    )
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 20
    assert all(-100 <= w <= 100 for w in radio.writes)
    assert check.evidence["restored"] is True


async def test_if_shift_profile_without_band_skips():
    """A profiled radio declaring no if_shift band SKIPs instead of assuming
    the hard-coded +/-1200 Hz band."""
    radio = _ShiftRadio(start=0, band=(-1200, 1200), controls={})
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.SKIP
    assert "no declared range" in check.evidence["reason"]
    assert radio.writes == []


# ---------------------------------------------------------------------------
# filter_width.set — branch from filter_width_encoding
# ---------------------------------------------------------------------------


async def test_filter_table_index_encoding_selects_index_branch():
    """A table_index profile treats the readback as a table code even above
    the legacy value threshold: 35 nudges to 30, not to an Hz value."""
    radio = _FilterRadio(start=35, encoding="table_index")
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 30
    assert check.evidence["readback"] == 30


async def test_filter_hz_encoding_selects_hz_branch():
    """An Hz-encoding profile treats the readback as Hz even below the legacy
    value threshold: 25 nudges by +200 Hz, not by an index delta."""
    radio = _FilterRadio(start=25, encoding="segmented_bcd_index")
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 225
    assert check.evidence["readback"] == 225


# ---------------------------------------------------------------------------
# nb.set / nr.set — "on" probe level from the profile band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["nb", "nr"])
async def test_nb_nr_on_level_comes_from_profile_band(name: str):
    """A profile declaring a 0-20 band drives the level-fallback "on" probe
    (mid-band 10) instead of the hard-coded 5."""
    radio = _NbNrLevelRadio(
        name=name,
        start=0,
        band=(0, 20),
        controls={name: {"range_min": 0, "range_max": 20}},
    )
    check = await _run(radio, check_id=f"{name}.set", capability=name)
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 10
    assert all(0 <= w <= 20 for w in radio.writes)
    assert check.evidence["restored"] is True


async def test_nb_level_profile_without_band_skips():
    """A profiled radio declaring no NB band SKIPs the level fallback instead
    of probing the hard-coded level."""
    radio = _NbNrLevelRadio(name="nb", start=0, band=(0, 10), controls={})
    check = await _run(radio, check_id="nb.set", capability="nb")
    assert check.status is CheckStatus.SKIP
    assert "no declared range" in check.evidence["reason"]
    assert radio.writes == []
