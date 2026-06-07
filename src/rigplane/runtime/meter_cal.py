"""Shared meter calibration helpers.

Calibration tables themselves live in per-rig TOML
(`rigs/*.toml` ``[[meters.<name>.calibration]]`` blocks) and are loaded at
runtime via :class:`rigplane.profiles.RadioProfile`. This module hosts the
algorithm consumed by every backend.
"""

from __future__ import annotations

__all__ = ["interpolate_swr", "interpolate_meter", "MeterType"]

from enum import Enum
from typing import Any


class MeterType(str, Enum):
    SMETER = "smeter"
    POWER = "power"
    SWR = "swr"
    ALC = "alc"
    CURRENT = "id"
    VOLTAGE = "vd"
    COMP = "comp"


def interpolate_meter(
    raw: int,
    meter_calibrations: dict[str, list[Any]] | None,
    meter_key: str,
) -> tuple[float, bool]:
    """Interpolate a raw meter reading against its calibration table.

    Looks up ``meter_key`` in ``meter_calibrations`` (loaded from TOML's
    ``[[meters.<meter_key>.calibration]]`` blocks) and, when a non-empty
    table is present, returns ``(actual, True)`` — the piecewise-linear
    interpolation between calibration points, clamped at the endpoints.

    When the table is absent or empty, returns ``(float(raw), False)``:
    the device-scale value flagged ``uncalibrated``. Callers MUST NOT
    treat that value as a calibrated reading; the second element of the
    tuple is the calibrated/uncalibrated marker.

    Mirrors ``yaesu_cat.radio._interpolate_swr`` so all backends share a
    single algorithm — the wfview piecewise-linear curve.
    """
    points = (meter_calibrations or {}).get(meter_key)
    if points:
        # Points are typically already sorted by raw, but sort defensively.
        sorted_pts = sorted(points, key=lambda p: p["raw"])
        if raw <= sorted_pts[0]["raw"]:
            return float(sorted_pts[0]["actual"]), True
        if raw >= sorted_pts[-1]["raw"]:
            return float(sorted_pts[-1]["actual"]), True
        for lo, hi in zip(sorted_pts, sorted_pts[1:]):
            if lo["raw"] <= raw <= hi["raw"]:
                span = hi["raw"] - lo["raw"]
                if span == 0:
                    return float(lo["actual"]), True
                t = (raw - lo["raw"]) / span
                return (
                    float(lo["actual"])
                    + t * (float(hi["actual"]) - float(lo["actual"])),
                    True,
                )
    # No table: device-scale sentinel, flagged uncalibrated.
    return float(raw), False


def interpolate_swr(raw: int, meter_calibrations: dict[str, list[Any]] | None) -> float:
    """Convert raw SWR meter value (0-255) to a calibrated SWR ratio.

    Uses the ``swr`` calibration table from ``meter_calibrations`` (loaded
    from TOML's ``[[meters.swr.calibration]]`` blocks) when available,
    interpolating piecewise-linearly between points. Returns the legacy
    linear approximation ``1.0 + raw/255 * 8.9`` when no table is
    configured (preserves backward compat for rigs that don't yet ship
    calibration data).

    Thin wrapper over :func:`interpolate_meter` for the ``swr`` table.
    """
    value, calibrated = interpolate_meter(raw, meter_calibrations, "swr")
    if calibrated:
        return value
    # No table: legacy linear fallback (pre-#440 behavior).
    if raw <= 0:
        return 1.0
    return 1.0 + (raw / 255.0) * 8.9
