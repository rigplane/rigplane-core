"""MOR-4352: CI-V RF power writes quantized to whole watts.

Live facts (Xiegu G7250, real radio, Mac mini bench, 2026-11-10): the radio
stores RF power in WHOLE WATTS. A write of raw ``r`` lands on
``W = floor(r * max_w / 255)`` (writing raw 12 with max 100 W stored 4 W)
and a read answers ``floor(W * 255 / max_w)`` (the 5 W menu minimum reads
raw 12, full power reads 255). Writing the raw that the readback admits —
the SMALLEST raw ``r`` with ``floor(r * max_w / 255) == W`` (5 W -> 13) —
keeps the normalized write path's exact-readback contract intact.

The mechanism is shared profile data (``[controls.rf_power]
write_quantum_w``), typed as ``PowerWriteQuantum`` and consumed by the one
encoder (``command_service.py: resolve_power_level_target``); radios
without the field behave exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.core.command_service import (
    command_intent_from_request,
    resolve_power_level_target,
)
from rigplane.profiles import PowerWriteQuantum, get_radio_profile
from rigplane.rig_loader import RigLoadError, load_rig

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_G7250_TOML = _RIGS_DIR / "g7250.toml"

# The G7250's declared whole-watt quantum over the measured raw band.
_G7250_QUANTUM = PowerWriteQuantum(quantum_w=1, raw_min=12, raw_max=255)


def _g7250_resolver_kwargs() -> dict[str, object]:
    return {
        "power_native_unit": "raw_255",
        "power_max_watts": 100,
        "power_write_quantum": _G7250_QUANTUM,
    }


# ---------------------------------------------------------------------------
# The resolver: normalized writes land on the whole-watt lattice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "expected_native", "expected_admitted"),
    [
        # The bench minimum request: the old encoder wrote raw 12, the radio
        # stored 4 W and read back 10 (LIVE 2026-11-10). 12/255 targets
        # 4.7 W -> the whole-watt step 5 W -> the smallest raw admitting it.
        (12 / 255, 13, 12 / 255),
        # A request that would land on 4 W (below the raw_min 12 band) is
        # clamped up to the 5 W floor, never the refused 4 W.
        (10 / 255, 13, 12 / 255),
        # The bottom of the normalized domain is the profile floor: the
        # lowest reachable watt is the one raw_min reads back as.
        (0.0, 13, 12 / 255),
        # Full power: raw 255 is its own smallest admitting raw.
        (1.0, 255, 1.0),
        # A mid value: 50 W is written as the smallest raw r with
        # floor(r * 100 / 255) == 50 (128, not 127) and admitted as the
        # radio's readback floor(50 * 255 / 100) = 127.
        (0.5, 128, 127 / 255),
    ],
)
def test_normalized_level_writes_the_smallest_raw_for_the_whole_watt_step(
    level: float, expected_native: int, expected_admitted: float
) -> None:
    assert resolve_power_level_target(
        level,
        **_g7250_resolver_kwargs(),  # type: ignore[arg-type]
    ) == (expected_native, expected_admitted)


@pytest.mark.parametrize(
    ("raw", "expected_admitted"),
    [
        # A bare int is the documented raw byte and goes out as written; the
        # admitted level follows the radio's storage model, so the pending
        # readback expectation can reconcile against the real answer.
        (13, 12 / 255),  # floor(13 * 100 / 255) = 5 W -> reads 12
        (12, 10 / 255),  # floor(12 * 100 / 255) = 4 W -> reads 10
        (255, 1.0),  # floor(255 * 100 / 255) = 100 W -> reads 255
    ],
)
def test_raw_int_write_keeps_the_byte_and_admits_the_radio_readback(
    raw: int, expected_admitted: float
) -> None:
    assert resolve_power_level_target(
        raw,
        **_g7250_resolver_kwargs(),  # type: ignore[arg-type]
    ) == (raw, expected_admitted)


def test_command_intent_admits_the_radio_readback_level() -> None:
    intent = command_intent_from_request(
        "set_rf_power",
        {"level": 12 / 255},
        source="http",
        **_g7250_resolver_kwargs(),  # type: ignore[arg-type]
    )
    # The stored power_level is the command's expected observation and the
    # HTTP reply's admitted_level; it must be the radio's readback (12/255),
    # not the requested 12/255-by-luck or the raw byte's own 13/255.
    assert intent.params["power_level"] == 12 / 255


# ---------------------------------------------------------------------------
# Radios without the field behave exactly as today
# ---------------------------------------------------------------------------


def test_without_the_quantum_the_same_request_still_writes_raw_12() -> None:
    # The IC-705 shape: raw_255, a valid max_watts, no declared quantum.
    # The same normalized request that the G7250 quantizes still writes the
    # raw byte directly (today's encoder, MOR-1579).
    assert resolve_power_level_target(
        12 / 255, power_native_unit="raw_255", power_max_watts=10
    ) == (12, 12 / 255)


def test_quantum_without_a_valid_max_watts_is_inert() -> None:
    # The loader guarantees [power].max_watts; a hand-built fact without it
    # keeps today's behaviour rather than guessing a scale.
    assert resolve_power_level_target(
        12 / 255,
        power_native_unit="raw_255",
        power_max_watts=None,
        power_write_quantum=_G7250_QUANTUM,
    ) == (12, 12 / 255)


def test_quantum_is_inert_on_watts_native_radios() -> None:
    # The quantum is a raw_255 (CI-V) mechanism; a watts-native radio
    # (Yaesu CAT PC) keeps its own clamped admission (MOR-2998).
    assert resolve_power_level_target(
        0.0,
        power_native_unit="watts",
        power_max_watts=100,
        power_min_watts=5,
        power_write_quantum=_G7250_QUANTUM,
    ) == (5, 0.05)


# ---------------------------------------------------------------------------
# Profile data: the G7250 declares the measured quantum
# ---------------------------------------------------------------------------


def test_g7250_profile_declares_the_live_write_quantum() -> None:
    profile = get_radio_profile("G7250")
    assert profile.power_write_quantum == _G7250_QUANTUM
    # Published on the control entry beside the raw band the Pro TX smoke
    # tool already reads.
    assert profile.controls is not None
    entry = profile.controls["rf_power"]
    assert entry["write_quantum_w"] == 1
    assert entry["raw_min"] == 12
    assert entry["raw_max"] == 255


def test_no_shipped_quantum_radio_keeps_a_none_quantum() -> None:
    # Every other shipped CI-V radio is untouched by the mechanism.
    for model in ("IC-705", "IC-7300", "X6200"):
        assert get_radio_profile(model).power_write_quantum is None, model


def _patched_g7250(tmp_path: Path, *replacements: tuple[str, str]) -> Path:
    """A copy of the shipped G7250 TOML with anchored text replaced.

    Every anchor must occur exactly once; the assert turns a drifted
    profile comment into a test failure instead of a silently unpatched
    load. The G7250 declares no tone capability, so no CTCSS catalog is
    needed next to the copy.
    """
    text = _G7250_TOML.read_text(encoding="utf-8")
    for old, new in replacements:
        assert text.count(old) == 1, f"anchor not unique: {old!r}"
        text = text.replace(old, new)
    broken = tmp_path / "g7250.toml"
    broken.write_text(text, encoding="utf-8")
    return broken


def test_loader_refuses_the_quantum_without_power_max_watts(tmp_path: Path) -> None:
    broken = _patched_g7250(tmp_path, ("max_watts = 100\n", ""))
    with pytest.raises(
        RigLoadError, match=r"write_quantum_w requires \[power\]\.max_watts"
    ):
        load_rig(broken)


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_loader_refuses_a_non_positive_quantum(tmp_path: Path, bad: str) -> None:
    broken = _patched_g7250(
        tmp_path, ("write_quantum_w = 1", f"write_quantum_w = {bad}")
    )
    with pytest.raises(RigLoadError, match=r"write_quantum_w must be > 0"):
        load_rig(broken)


def test_loader_refuses_the_quantum_on_another_control(tmp_path: Path) -> None:
    broken = _patched_g7250(
        tmp_path,
        ("write_quantum_w = 1\n", ""),
        (
            "[controls.af_level]\nraw_min = 0",
            "[controls.af_level]\nraw_min = 0\nwrite_quantum_w = 1",
        ),
    )
    with pytest.raises(RigLoadError, match=r"only valid on \[controls\.rf_power\]"):
        load_rig(broken)


def test_loader_refuses_the_quantum_without_the_raw_band(tmp_path: Path) -> None:
    broken = _patched_g7250(
        tmp_path,
        # Comment the band's low end away: write_quantum_w is left without
        # the raw_min it must be declared with.
        ("raw_min = 12", "# raw band removed"),
    )
    with pytest.raises(RigLoadError, match=r"write_quantum_w requires"):
        load_rig(broken)
