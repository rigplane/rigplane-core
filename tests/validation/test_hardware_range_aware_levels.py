"""MOR-695: level RMVR value rules are range-aware.

The MOR-679 level checks used a fixed ICOM-0-255 nudge (``200 if v < 128 else
50``). On radios whose comp/nr/nb levels have SMALL ranges (the Yaesu FTX-1:
nr/nb 0-10, comp 0-100), that nudge lands out of range, the radio ignores the
write, and the readback equals the original -> false FAIL.

This module proves the range-aware nudge:

* resolves the control's ``[min, max]`` band from ``radio.profile.controls``
  via an ordered candidate-key list (FTX-1 uses ``nr``/``nb``; IC-7610 uses
  ``nr_level``/``nb_level``/``compressor_level``);
* PASSes, reacts, and restores for nr 0-10, nb 0-10, comp 0-100, and the
  Icom-style 0-255 controls;
* NEVER writes a value outside the resolved band (asserted on every write);
* steps DOWN when the original sits at the ceiling;
* defaults to 0-255 when no range is declared (rf_power/mic_gain).
"""

from __future__ import annotations

from rigplane.core.radio_state import RadioState
from rigplane.validation.hardware import (
    _range_aware_level_nudge,
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
    model: str,
    profile_id: str,
    check_id: str,
    capability: str,
    declaration: CapabilityDeclaration = CapabilityDeclaration.SUPPORTED,
) -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model=model, profile_id=profile_id),
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


class _FakeProfile:
    """Minimal stand-in for ``RadioProfile`` exposing only ``controls``."""

    def __init__(self, controls: dict[str, dict]) -> None:
        self.controls = controls


class _StatefulLevelRadio:
    """Stateful fake round-tripping set->get for level controls, clamping to a
    per-control band so an out-of-range write is silently ignored (matching a
    real radio that NAKs/snaps). Records every write so a test can assert no
    value ever left the band, and exposes a ``profile.controls`` dict so the
    range resolver can read declared ranges.
    """

    def __init__(
        self,
        *,
        capabilities: set[str],
        controls: dict[str, dict] | None,
        initial: dict[str, int],
        bands: dict[str, tuple[int, int]],
    ) -> None:
        self.connected = True
        self.model = "FAKE"
        self.capabilities = capabilities
        self.radio_state = RadioState()
        self.profile = _FakeProfile(controls) if controls is not None else None
        self._values = dict(initial)
        self._bands = bands
        self.writes: dict[str, list[int]] = {k: [] for k in initial}

    def _set(self, key: str, level: int) -> None:
        self.writes[key].append(level)
        lo, hi = self._bands[key]
        # A real radio ignores an out-of-band write -> value unchanged.
        if lo <= level <= hi:
            self._values[key] = level

    async def get_rf_power(self) -> int:
        return self._values["rf_power"]

    async def set_rf_power(self, level: int) -> None:
        self._set("rf_power", level)

    async def get_mic_gain(self) -> int:
        return self._values["mic_gain"]

    async def set_mic_gain(self, level: int) -> None:
        self._set("mic_gain", level)

    async def get_compressor_level(self) -> int:
        return self._values["comp"]

    async def set_compressor_level(self, level: int) -> None:
        self._set("comp", level)

    async def get_nr_level(self, receiver: int = 0) -> int:
        return self._values["nr"]

    async def set_nr_level(self, level: int, receiver: int = 0) -> None:
        self._set("nr", level)

    async def get_nb_level(self, receiver: int = 0) -> int:
        return self._values["nb"]

    async def set_nb_level(self, level: int, receiver: int = 0) -> None:
        self._set("nb", level)


_ALL_CAPS = {"power_control", "compressor", "nr", "nb"}


def _make_radio(
    *,
    controls: dict[str, dict] | None,
    initial: dict[str, int] | None = None,
    bands: dict[str, tuple[int, int]] | None = None,
) -> _StatefulLevelRadio:
    return _StatefulLevelRadio(
        capabilities=_ALL_CAPS,
        controls=controls,
        initial=initial or {"rf_power": 0, "mic_gain": 0, "comp": 0, "nr": 0, "nb": 0},
        bands=bands
        or {
            "rf_power": (0, 255),
            "mic_gain": (0, 255),
            "comp": (0, 255),
            "nr": (0, 255),
            "nb": (0, 255),
        },
    )


# --- unit: the nudge helper ------------------------------------------------


def test_range_aware_nudge_small_range_in_band():
    nudge = _range_aware_level_nudge(0, 10)
    for orig in range(0, 11):
        result = nudge(orig)
        assert 0 <= result <= 10, f"{orig} -> {result} left [0, 10]"
        assert result != orig, f"{orig} -> {result} did not change"


def test_range_aware_nudge_steps_down_at_ceiling():
    # 0-10: step = max(1, 1) = 1; 10 + 1 > 10 -> step DOWN to 9.
    assert _range_aware_level_nudge(0, 10)(10) == 9
    # 0-100: step = 10; 100 + 10 > 100 -> step DOWN to 90.
    assert _range_aware_level_nudge(0, 100)(100) == 90
    # 0-255: step = 25; 255 + 25 > 255 -> step DOWN to 230.
    assert _range_aware_level_nudge(0, 255)(255) == 230


def test_range_aware_nudge_steps_up_off_floor():
    assert _range_aware_level_nudge(0, 10)(0) == 1
    assert _range_aware_level_nudge(0, 100)(0) == 10
    assert _range_aware_level_nudge(0, 255)(0) == 25


# --- unit: range resolution from profile -----------------------------------


def test_resolve_range_candidate_keys_ftx1_style():
    radio = _make_radio(controls={"nr": {"range_min": 0, "range_max": 10}})
    # nr_level.set resolves via candidate "nr" (FTX-1 key) -> 0-10.
    assert _resolve_level_range(radio, "nr_level.set") == (0, 10)


def test_resolve_range_reads_raw_min_max_icom_style():
    radio = _make_radio(controls={"compressor_level": {"raw_min": 0, "raw_max": 255}})
    # IC-7610 declares raw_min/raw_max (not range_min/range_max) -> 0-255.
    assert _resolve_level_range(radio, "comp_level.set") == (0, 255)


def test_resolve_range_defaults_to_0_255_when_undeclared():
    radio = _make_radio(controls={})  # nothing declared
    assert _resolve_level_range(radio, "rf_power.set") == (0, 255)
    assert _resolve_level_range(radio, "mic_gain.set") == (0, 255)


def test_resolve_range_no_profile_defaults_to_0_255():
    radio = _make_radio(controls=None)  # radio has no profile
    assert _resolve_level_range(radio, "nb_level.set") == (0, 255)


# --- integration: RMVR through execute_hardware_checks ---------------------


# (check_id, capability, control-key, declared-controls)
_SMALL_RANGE_CASES = [
    ("nr_level.set", "nr", "nr", {"nr": {"range_min": 0, "range_max": 10}}),
    ("nb_level.set", "nb", "nb", {"nb": {"range_min": 0, "range_max": 10}}),
    (
        "comp_level.set",
        "compressor",
        "comp",
        {"compressor_level": {"range_min": 0, "range_max": 100}},
    ),
]


async def test_small_range_levels_pass_react_restore_and_stay_in_band():
    """nr/nb 0-10 and comp 0-100 each PASS, react, restore, never leave band."""
    for check_id, capability, key, controls in _SMALL_RANGE_CASES:
        radio = _make_radio(controls=controls)
        lo, hi = _resolve_level_range(radio, check_id)
        mid = (lo + hi) // 2
        radio = _make_radio(
            controls=controls,
            initial={
                "rf_power": 100,
                "mic_gain": 100,
                "comp": mid,
                "nr": mid,
                "nb": mid,
            },
            bands={
                "rf_power": (0, 255),
                "mic_gain": (0, 255),
                "comp": (lo, hi) if key == "comp" else (0, 100),
                "nr": (lo, hi) if key == "nr" else (0, 10),
                "nb": (lo, hi) if key == "nb" else (0, 10),
            },
        )
        template = _single_entry_template(
            model="FTX-1",
            profile_id="ftx1",
            check_id=check_id,
            capability=capability,
        )
        levels = await execute_hardware_checks(
            radio, template, OperatorSafetyBlock(), allow_writes=True
        )
        check = _flatten(levels)[check_id]
        assert check.status is CheckStatus.PASS, (
            f"{check_id}: expected PASS, got {check.status} ({check.error})"
        )
        assert check.evidence["restored"] is True
        writes = radio.writes[key]
        assert writes, f"{check_id}: expected at least one write"
        for value in writes:
            assert lo <= value <= hi, (
                f"{check_id}: wrote {value} outside resolved band [{lo}, {hi}]"
            )
        assert writes[-1] == mid, f"{check_id}: not restored to original {mid}"


async def test_small_range_level_at_ceiling_steps_down_and_passes():
    """Original at the ceiling (nr=10) nudges DOWN and still PASSes."""
    radio = _make_radio(
        controls={"nr": {"range_min": 0, "range_max": 10}},
        initial={"rf_power": 100, "mic_gain": 100, "comp": 50, "nr": 10, "nb": 5},
        bands={
            "rf_power": (0, 255),
            "mic_gain": (0, 255),
            "comp": (0, 100),
            "nr": (0, 10),
            "nb": (0, 10),
        },
    )
    template = _single_entry_template(
        model="FTX-1", profile_id="ftx1", check_id="nr_level.set", capability="nr"
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["nr_level.set"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 9  # 10 - 1, stepped away from ceiling
    assert radio.writes["nr"][-1] == 10  # restored


async def test_undeclared_range_levels_nudge_in_0_255():
    """rf_power/mic_gain without a declared control still nudge inside 0-255."""
    for check_id, key in (("rf_power.set", "rf_power"), ("mic_gain.set", "mic_gain")):
        radio = _make_radio(
            controls={},  # no control declarations -> default 0-255
            initial={"rf_power": 100, "mic_gain": 100, "comp": 50, "nr": 5, "nb": 5},
            bands={
                "rf_power": (0, 255),
                "mic_gain": (0, 255),
                "comp": (0, 100),
                "nr": (0, 10),
                "nb": (0, 10),
            },
        )
        template = _single_entry_template(
            model="FTX-1",
            profile_id="ftx1",
            check_id=check_id,
            capability="power_control",
        )
        levels = await execute_hardware_checks(
            radio, template, OperatorSafetyBlock(), allow_writes=True
        )
        check = _flatten(levels)[check_id]
        assert check.status is CheckStatus.PASS, (
            f"{check_id}: expected PASS, got {check.status} ({check.error})"
        )
        writes = radio.writes[key]
        assert writes, f"{check_id}: expected at least one write"
        for value in writes:
            assert 0 <= value <= 255, f"{check_id}: wrote {value} outside [0, 255]"
        assert writes[-1] == 100  # restored


async def test_out_of_band_original_skips_rather_than_writes():
    """An original outside the declared band SKIPs (never an unrestorable write)."""
    radio = _make_radio(
        controls={"nr": {"range_min": 0, "range_max": 10}},
        # nr starts at 99 — outside the declared 0-10 band.
        initial={"rf_power": 100, "mic_gain": 100, "comp": 50, "nr": 99, "nb": 5},
        bands={
            "rf_power": (0, 255),
            "mic_gain": (0, 255),
            "comp": (0, 100),
            "nr": (0, 255),  # radio would accept it, but harness must not risk it
            "nb": (0, 10),
        },
    )
    template = _single_entry_template(
        model="FTX-1", profile_id="ftx1", check_id="nr_level.set", capability="nr"
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["nr_level.set"]
    assert check.status is CheckStatus.SKIP
    assert radio.writes["nr"] == []  # never wrote
