"""MOR-679: RMVR checks for Icom level controls.

Covers the six level RMVR checks added by MOR-679 — rf_power, mic_gain,
comp_level, nr_level, nb_level, cw_pitch — which share the CoreRadio backend
(serving both IC-7610 and IC-7300). Each check is exercised against a stateful
in-process fake that round-trips set->get, asserting it:

* PASSes (the readback reacts and the original is restored),
* restores the original value, and
* never writes a value outside the control's documented range.

MOR-2476: the ranges come from ``profile.controls`` (mirroring
``rigs/ic7610.toml``), never a hardcoded default — a radio whose profile
declares no band for a control SKIPs honestly instead of assuming 0-255.

A profile that does NOT declare the gating capability resolves the check to
UNSUPPORTED without crashing.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from rigplane.core.radio_state import RadioState
from rigplane.validation.hardware import (
    _resolve_cw_pitch_band,
    _resolve_level_range,
    execute_hardware_checks,
)
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


def _single_entry_template(
    *,
    check_id: str,
    capability: str,
    declaration: CapabilityDeclaration = CapabilityDeclaration.SUPPORTED,
) -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="IC-7610", profile_id="icom_ic7610"),
        entries=[
            CapabilityDeclarationEntry(
                check_id=check_id,
                capability=capability,
                level=ValidationLevel.CAPABILITY_MATRIX,
                declaration=declaration,
                summary="single",
            )
        ],
    )


# The IC-7610 control shapes as published by rigs/ic7610.toml: raw-only level
# controls except nr_level, which also carries a legacy 0-15 panel-display
# band over the 0-255 raw wire scale, and a legacy cw_pitch display pair.
_IC7610_CONTROLS: dict[str, dict] = {
    "rf_power": {"raw_min": 0, "raw_max": 255},
    "mic_gain": {"raw_min": 0, "raw_max": 255},
    "compressor_level": {"raw_min": 0, "raw_max": 255},
    "nr_level": {"raw_min": 0, "raw_max": 255, "display_min": 0, "display_max": 15},
    "nb_level": {"raw_min": 0, "raw_max": 255},
    "cw_pitch": {
        "raw_min": 0,
        "raw_max": 255,
        "display_min": 300,
        "display_max": 900,
        "display_unit": "Hz",
    },
}

# The X6200 cw_pitch shape as published by rigs/x6200.toml: same legacy raw
# 0-255 domain, but its profile-declared display band is 400-1200 Hz.
_X6200_CW_PITCH_CONTROLS: dict[str, dict] = {
    "cw_pitch": {
        "raw_min": 0,
        "raw_max": 255,
        "display_min": 400,
        "display_max": 1200,
        "display_unit": "Hz",
    },
}


class _StatefulIcomLevelRadio:
    """Stateful fake that round-trips set->get for the six MOR-679 controls.

    Mirrors the shared CoreRadio surface: 0-255 levels for rf_power/mic_gain/
    compressor/nr/nb plus CW pitch in Hz snapped to a 5 Hz grid (matching the
    real decode). Records every written value so a test can assert nothing was
    ever written out of range. ``capabilities`` is parameterised so the
    cap-undeclared path can be exercised, and ``controls`` carries the
    profile-declared control tables the bands are resolved from.
    """

    def __init__(
        self,
        capabilities: set[str],
        *,
        controls: dict[str, dict] | None = None,
        cw_pitch_bounds: tuple[int, int] = (300, 900),
    ) -> None:
        self.connected = True
        self.model = "IC-7610"
        self.capabilities = capabilities
        self.radio_state = RadioState()
        self.profile = (
            SimpleNamespace(controls=controls) if controls is not None else None
        )
        self._cw_pitch_bounds = cw_pitch_bounds
        self._rf_power = 100
        self._mic_gain = 100
        self._comp_level = 100
        self._nr_level = 100
        self._nb_level = 100
        self._cw_pitch = 600
        self.rf_power_writes: list[int] = []
        self.mic_gain_writes: list[int] = []
        self.comp_writes: list[int] = []
        self.nr_writes: list[int] = []
        self.nb_writes: list[int] = []
        self.cw_pitch_writes: list[int] = []

    async def get_rf_power(self) -> int:
        return self._rf_power

    async def set_rf_power(self, level: int) -> None:
        assert 0 <= level <= 255, f"rf_power out of range: {level}"
        self.rf_power_writes.append(level)
        self._rf_power = level

    async def get_mic_gain(self) -> int:
        return self._mic_gain

    async def set_mic_gain(self, level: int) -> None:
        assert 0 <= level <= 255, f"mic_gain out of range: {level}"
        self.mic_gain_writes.append(level)
        self._mic_gain = level

    async def get_compressor_level(self) -> int:
        return self._comp_level

    async def set_compressor_level(self, level: int) -> None:
        assert 0 <= level <= 255, f"comp_level out of range: {level}"
        self.comp_writes.append(level)
        self._comp_level = level

    async def get_nr_level(self, receiver: int = 0) -> int:
        return self._nr_level

    async def set_nr_level(self, level: int, receiver: int = 0) -> None:
        assert 0 <= level <= 255, f"nr_level out of range: {level}"
        self.nr_writes.append(level)
        self._nr_level = level

    async def get_nb_level(self, receiver: int = 0) -> int:
        return self._nb_level

    async def set_nb_level(self, level: int, receiver: int = 0) -> None:
        assert 0 <= level <= 255, f"nb_level out of range: {level}"
        self.nb_writes.append(level)
        self._nb_level = level

    async def get_cw_pitch(self) -> int:
        # Real decode snaps to the nearest 5 Hz; the fake stores on-grid values.
        return self._cw_pitch

    async def set_cw_pitch(self, pitch_hz: int) -> None:
        lo, hi = self._cw_pitch_bounds
        assert lo <= pitch_hz <= hi, f"cw_pitch out of range: {pitch_hz}"
        self.cw_pitch_writes.append(pitch_hz)
        self._cw_pitch = pitch_hz


_ALL_CAPS = {"power_control", "compressor", "nr", "nb", "cw"}

# (check_id, capability, writes-attr, original-value)
_CASES = [
    ("rf_power.set", "power_control", "rf_power_writes", 100),
    ("mic_gain.set", "power_control", "mic_gain_writes", 100),
    ("comp_level.set", "compressor", "comp_writes", 100),
    ("nr_level.set", "nr", "nr_writes", 100),
    ("nb_level.set", "nb", "nb_writes", 100),
    ("cw_pitch.set", "cw", "cw_pitch_writes", 600),
]


async def test_icom_level_rmvr_pass_and_restore():
    """Each level RMVR PASSes, reacts, and restores the original value."""
    for check_id, capability, writes_attr, original in _CASES:
        radio = _StatefulIcomLevelRadio(_ALL_CAPS, controls=_IC7610_CONTROLS)
        template = _single_entry_template(check_id=check_id, capability=capability)
        levels = await execute_hardware_checks(
            radio, template, OperatorSafetyBlock(), allow_writes=True
        )
        check = _flatten(levels)[check_id]
        assert check.status is CheckStatus.PASS, (
            f"{check_id}: expected PASS, got {check.status} ({check.error})"
        )
        assert check.evidence["original"] == original
        assert check.evidence["restored"] is True
        # The control must end the run back at its original value.
        writes = getattr(radio, writes_attr)
        assert writes[-1] == original, (
            f"{check_id}: last write {writes[-1]} != original {original}"
        )


async def test_icom_level_rmvr_never_writes_out_of_range():
    """No level RMVR ever writes a value outside the control's documented band."""
    for check_id, capability, writes_attr, _original in _CASES:
        radio = _StatefulIcomLevelRadio(_ALL_CAPS, controls=_IC7610_CONTROLS)
        template = _single_entry_template(check_id=check_id, capability=capability)
        await execute_hardware_checks(
            radio, template, OperatorSafetyBlock(), allow_writes=True
        )
        writes = getattr(radio, writes_attr)
        assert writes, f"{check_id}: expected at least one write"
        if check_id == "cw_pitch.set":
            lo, hi = 300, 900
        else:
            lo, hi = 0, 255
        for value in writes:
            assert lo <= value <= hi, f"{check_id}: wrote {value} outside [{lo}, {hi}]"


async def test_icom_level_rmvr_unsupported_when_cap_undeclared():
    """A profile not declaring the gating capability resolves UNSUPPORTED."""
    for check_id, capability, _writes_attr, _original in _CASES:
        # Radio declares NO capabilities at all — gate resolves not-present.
        radio = _StatefulIcomLevelRadio(set(), controls=_IC7610_CONTROLS)
        template = _single_entry_template(check_id=check_id, capability=capability)
        levels = await execute_hardware_checks(
            radio, template, OperatorSafetyBlock(), allow_writes=True
        )
        check = _flatten(levels)[check_id]
        assert check.status is CheckStatus.UNSUPPORTED, (
            f"{check_id}: expected UNSUPPORTED, got {check.status}"
        )
        assert check.evidence.get("capability_present") is False


async def test_cw_pitch_nudge_stays_on_grid_at_ceiling():
    """CW pitch near the 900 Hz ceiling nudges DOWN, never out of range."""
    radio = _StatefulIcomLevelRadio(_ALL_CAPS, controls=_IC7610_CONTROLS)
    radio._cw_pitch = 900
    template = _single_entry_template(check_id="cw_pitch.set", capability="cw")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["cw_pitch.set"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 850  # 900 - 50, stepped away from ceiling
    assert radio.cw_pitch_writes[-1] == 900  # restored


# --- MOR-2476: bands come from the profile, never a default ------------------


def test_level_bands_resolve_from_the_profile_raw_axis():
    """Every IC-7610 level check resolves its band from profile.controls.

    nr_level additionally declares a legacy 0-15 panel-display band over the
    0-255 raw wire scale; the level wire ops write the raw scale, so the raw
    pair is the band — never the display pair, never a hardcoded default.
    """
    radio = _StatefulIcomLevelRadio(_ALL_CAPS, controls=_IC7610_CONTROLS)
    expected = {
        "rf_power.set": (0, 255),
        "mic_gain.set": (0, 255),
        "comp_level.set": (0, 255),
        "nr_level.set": (0, 255),
        "nb_level.set": (0, 255),
    }
    for check_id, band in expected.items():
        assert _resolve_level_range(radio, check_id) == band, check_id


def test_cw_pitch_band_resolves_from_the_profile_display_pair():
    """The IC-7610 cw_pitch band is its legacy display pair, 300-900 Hz."""
    radio = _StatefulIcomLevelRadio(_ALL_CAPS, controls=_IC7610_CONTROLS)
    assert _resolve_cw_pitch_band(radio) == (Decimal(300), Decimal(900), None)


async def test_level_set_skips_when_profile_declares_no_range():
    """No profile at all -> honest SKIP, no assumed 0-255 band, no writes."""
    radio = _StatefulIcomLevelRadio(_ALL_CAPS)  # no profile
    template = _single_entry_template(
        check_id="rf_power.set", capability="power_control"
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["rf_power.set"]
    assert check.status is CheckStatus.SKIP
    assert check.evidence["reason"] == (
        "no declared range for control 'rf_power' in the active profile; "
        "refusing to assume one"
    )
    assert radio.rf_power_writes == []


async def test_cw_pitch_set_skips_when_profile_declares_no_band():
    """A profile without a cw_pitch control SKIPs cw_pitch.set honestly."""
    controls = {k: v for k, v in _IC7610_CONTROLS.items() if k != "cw_pitch"}
    radio = _StatefulIcomLevelRadio(_ALL_CAPS, controls=controls)
    template = _single_entry_template(check_id="cw_pitch.set", capability="cw")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["cw_pitch.set"]
    assert check.status is CheckStatus.SKIP
    assert check.evidence["reason"] == (
        "no declared range for control 'cw_pitch' in the active profile; "
        "refusing to assume one"
    )
    assert radio.cw_pitch_writes == []


async def test_x6200_cw_pitch_uses_profile_band_400_1200():
    """The X6200 profile declares 400-1200 Hz, so that — not 300-900 — is the
    band its cw_pitch RMVR respects (MOR-2476 correctness fix)."""
    radio = _StatefulIcomLevelRadio(
        _ALL_CAPS,
        controls=_X6200_CW_PITCH_CONTROLS,
        cw_pitch_bounds=(400, 1200),
    )
    radio._cw_pitch = 800
    template = _single_entry_template(check_id="cw_pitch.set", capability="cw")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["cw_pitch.set"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 850  # 800 + 50, inside 400-1200
    assert radio.cw_pitch_writes[-1] == 800  # restored
    # At the ceiling the nudge steps DOWN, never above the declared band.
    radio2 = _StatefulIcomLevelRadio(
        _ALL_CAPS,
        controls=_X6200_CW_PITCH_CONTROLS,
        cw_pitch_bounds=(400, 1200),
    )
    radio2._cw_pitch = 1200
    levels2 = await execute_hardware_checks(
        radio2, template, OperatorSafetyBlock(), allow_writes=True
    )
    check2 = _flatten(levels2)["cw_pitch.set"]
    assert check2.status is CheckStatus.PASS
    assert check2.evidence["changed"] == 1150  # 1200 - 50, stepped away from ceiling
    for value in radio.cw_pitch_writes + radio2.cw_pitch_writes:
        assert 400 <= value <= 1200
