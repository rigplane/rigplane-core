"""MOR-2469 — rigctld quantizes IFSHIFT/CWPITCH to the profile lattice.

`YaesuRouting.set_level` used to forward `round(value)` (IFSHIFT) or a
hardcoded 300-1050 clamp (CWPITCH) while the Yaesu backend rejects any
value off the profile's raw lattice with ``ValueError`` → ``RPRT EINVAL``.
These tests pin the new behaviour: when the radio's profile publishes a
normalized control domain, the routing quantizes the requested value to
the nearest lattice point before calling the setter, and returns EINVAL
without calling the setter only when the value is out of range.
Radios without a normalized domain keep the legacy passthrough/clamp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rigplane.profiles.rig_loader import (
    load_rig,
    quantize_control_raw_value,
    validate_control_raw_value,
)
from rigplane.rigctld.contract import HamlibError
from rigplane.rigctld.routing import YaesuRouting

_RIGS_DIR = Path(__file__).parents[1] / "rigs"


@pytest.fixture(scope="module")
def ftx1_profile() -> Any:
    """The real FTX-1 RadioProfile, loaded the way production loads it."""
    return load_rig(_RIGS_DIR / "ftx1.toml").to_profile()


@pytest.fixture(scope="module")
def ftx1_controls(ftx1_profile: Any) -> dict[str, Any]:
    return dict(ftx1_profile.controls or {})


@pytest.fixture(scope="module")
def ic7300_profile() -> Any:
    """IC-7300 profile: legacy cw_pitch shape (no raw_step/raw_origin),
    no if_shift domain at all."""
    return load_rig(_RIGS_DIR / "ic7300.toml").to_profile()


# ---------------------------------------------------------------------------
# Helper: quantize_control_raw_value
# ---------------------------------------------------------------------------


def test_quantize_on_lattice_value_unchanged(ftx1_controls: dict[str, Any]) -> None:
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 40) == 40
    assert quantize_control_raw_value(ftx1_controls, "if_shift", -1200) == -1200
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 1200) == 1200
    assert quantize_control_raw_value(ftx1_controls, "cw_pitch", 700) == 700
    assert quantize_control_raw_value(ftx1_controls, "cw_pitch", 300) == 300
    assert quantize_control_raw_value(ftx1_controls, "cw_pitch", 1050) == 1050


def test_quantize_nearest_between_points(ftx1_controls: dict[str, Any]) -> None:
    # if_shift: step 20, origin 0 — 29 is nearer 20, 31 nearer 40.
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 29) == 20
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 31) == 40
    assert quantize_control_raw_value(ftx1_controls, "if_shift", -29) == -20
    # cw_pitch: step 10, origin 300.
    assert quantize_control_raw_value(ftx1_controls, "cw_pitch", 304) == 300
    assert quantize_control_raw_value(ftx1_controls, "cw_pitch", 306) == 310


def test_quantize_tie_rounds_away_from_origin(ftx1_controls: dict[str, Any]) -> None:
    """Pinned tie rule: exactly halfway rounds AWAY from raw_origin."""
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 10) == 20
    assert quantize_control_raw_value(ftx1_controls, "if_shift", -10) == -20
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 15) == 20
    assert quantize_control_raw_value(ftx1_controls, "cw_pitch", 305) == 310


def test_quantize_out_of_range_raises(ftx1_controls: dict[str, Any]) -> None:
    for value in (1300, -1210, 1201, -1201):
        with pytest.raises(ValueError, match="if_shift"):
            quantize_control_raw_value(ftx1_controls, "if_shift", value)
    for value in (1060, 299, 1051):
        with pytest.raises(ValueError, match="cw_pitch"):
            quantize_control_raw_value(ftx1_controls, "cw_pitch", value)


def test_quantize_float_values(ftx1_controls: dict[str, Any]) -> None:
    """rigctld hands the routing floats; quantization handles them."""
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 15.0) == 20
    assert quantize_control_raw_value(ftx1_controls, "if_shift", 20.4) == 20
    assert quantize_control_raw_value(ftx1_controls, "cw_pitch", 300.9) == 300


def test_quantize_legacy_domain_returns_none(ic7300_profile: Any) -> None:
    controls = ic7300_profile.controls
    assert quantize_control_raw_value(controls, "cw_pitch", 600) is None
    assert quantize_control_raw_value(controls, "if_shift", 600) is None


def test_quantize_absent_domain_returns_none() -> None:
    assert quantize_control_raw_value(None, "if_shift", 600) is None
    assert quantize_control_raw_value({}, "if_shift", 600) is None
    legacy = {"cw_pitch": {"raw_min": 0, "raw_max": 255}}
    assert quantize_control_raw_value(legacy, "cw_pitch", 600) is None
    partial = {"cw_pitch": {"raw_min": 0, "raw_max": 255, "raw_step": 10}}
    assert quantize_control_raw_value(partial, "cw_pitch", 600) is None


def test_quantize_agrees_with_validate_on_lattice(
    ftx1_controls: dict[str, Any],
) -> None:
    """Every quantized result must satisfy the backend's own validator."""
    for value in (-1200, -1195, -11, 0, 10, 15, 29, 700, 1049, 1200):
        quantized = quantize_control_raw_value(ftx1_controls, "if_shift", value)
        assert quantized is not None
        validate_control_raw_value(ftx1_controls, "if_shift", quantized)
    for value in (300, 305, 301, 555, 1049, 1050):
        quantized = quantize_control_raw_value(ftx1_controls, "cw_pitch", value)
        assert quantized is not None
        validate_control_raw_value(ftx1_controls, "cw_pitch", quantized)


# ---------------------------------------------------------------------------
# Routing doubles
# ---------------------------------------------------------------------------


class _ValidatingRadio:
    """Radio double whose setters validate exactly like YaesuCatRadio."""

    def __init__(self, profile: Any) -> None:
        self.profile = profile
        self.if_shift_calls: list[int] = []
        self.cw_pitch_calls: list[int] = []

    async def set_if_shift(self, offset: int, receiver: int = 0) -> None:
        validate_control_raw_value(self.profile.controls, "if_shift", offset)
        self.if_shift_calls.append(offset)

    async def set_cw_pitch(self, freq: int) -> None:
        validate_control_raw_value(self.profile.controls, "cw_pitch", freq)
        self.cw_pitch_calls.append(freq)


class _PassthroughRadio:
    """Radio double with no usable profile domain (Icom-style backend)."""

    profile: Any = None

    def __init__(self) -> None:
        self.if_shift_calls: list[int] = []
        self.cw_pitch_calls: list[int] = []

    async def set_if_shift(self, offset: int, receiver: int = 0) -> None:
        self.if_shift_calls.append(offset)

    async def set_cw_pitch(self, freq: int) -> None:
        self.cw_pitch_calls.append(freq)


def _routing(radio: Any) -> YaesuRouting:
    return YaesuRouting(radio, cache=object(), max_power_w=100.0)


# ---------------------------------------------------------------------------
# Routing: FTX-1 profile (normalized domains) — quantize or reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ifshift_quantizes_nearest(ftx1_profile: Any) -> None:
    radio = _ValidatingRadio(ftx1_profile)
    resp = await _routing(radio).set_level("IFSHIFT", 15)
    assert resp.ok
    assert radio.if_shift_calls == [20]


@pytest.mark.asyncio
async def test_ifshift_on_lattice_passthrough(ftx1_profile: Any) -> None:
    radio = _ValidatingRadio(ftx1_profile)
    resp = await _routing(radio).set_level("IFSHIFT", 20)
    assert resp.ok
    assert radio.if_shift_calls == [20]
    resp = await _routing(radio).set_level("IFSHIFT", -1200)
    assert resp.ok
    assert radio.if_shift_calls == [20, -1200]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1210, 1300])
async def test_ifshift_out_of_range_einval_no_call(
    ftx1_profile: Any, value: int
) -> None:
    radio = _ValidatingRadio(ftx1_profile)
    resp = await _routing(radio).set_level("IFSHIFT", value)
    assert resp.error == int(HamlibError.EINVAL)
    assert not resp.ok
    assert radio.if_shift_calls == []


@pytest.mark.asyncio
async def test_cwpitch_quantizes_nearest(ftx1_profile: Any) -> None:
    radio = _ValidatingRadio(ftx1_profile)
    resp = await _routing(radio).set_level("CWPITCH", 301)
    assert resp.ok
    assert radio.cw_pitch_calls == [300]


@pytest.mark.asyncio
async def test_cwpitch_tie_rounds_away_from_origin(ftx1_profile: Any) -> None:
    radio = _ValidatingRadio(ftx1_profile)
    resp = await _routing(radio).set_level("CWPITCH", 305)
    assert resp.ok
    assert radio.cw_pitch_calls == [310]


@pytest.mark.asyncio
async def test_cwpitch_bounds(ftx1_profile: Any) -> None:
    radio = _ValidatingRadio(ftx1_profile)
    resp = await _routing(radio).set_level("CWPITCH", 1050)
    assert resp.ok
    assert radio.cw_pitch_calls == [1050]


@pytest.mark.asyncio
async def test_cwpitch_out_of_range_einval_no_call(ftx1_profile: Any) -> None:
    radio = _ValidatingRadio(ftx1_profile)
    resp = await _routing(radio).set_level("CWPITCH", 1060)
    assert resp.error == int(HamlibError.EINVAL)
    assert radio.cw_pitch_calls == []


# ---------------------------------------------------------------------------
# Routing: no normalized domain — today's path unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ifshift_no_domain_keeps_round_passthrough() -> None:
    radio = _PassthroughRadio()
    resp = await _routing(radio).set_level("IFSHIFT", 15.7)
    assert resp.ok
    assert radio.if_shift_calls == [16]


@pytest.mark.asyncio
async def test_cwpitch_no_domain_keeps_legacy_clamp() -> None:
    radio = _PassthroughRadio()
    resp = await _routing(radio).set_level("CWPITCH", 200)
    assert resp.ok
    assert radio.cw_pitch_calls == [300]
    resp = await _routing(radio).set_level("CWPITCH", 5000)
    assert resp.ok
    assert radio.cw_pitch_calls == [300, 1050]
    resp = await _routing(radio).set_level("CWPITCH", 700)
    assert resp.ok
    assert radio.cw_pitch_calls == [300, 1050, 700]


@pytest.mark.asyncio
async def test_cwpitch_real_ic7300_profile_keeps_legacy_clamp(
    ic7300_profile: Any,
) -> None:
    """The real IC-7300 profile publishes a legacy cw_pitch shape — the
    routing must take the legacy clamp, not attempt lattice quantization."""
    radio = _PassthroughRadio()
    radio.profile = ic7300_profile
    resp = await _routing(radio).set_level("CWPITCH", 205)
    assert resp.ok
    assert radio.cw_pitch_calls == [300]
