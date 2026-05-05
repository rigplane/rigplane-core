"""Shared meter calibration helpers.

Calibration tables themselves live in per-rig TOML
(`rigs/*.toml` ``[[meters.<name>.calibration]]`` blocks) and are loaded at
runtime via :class:`icom_lan.profiles.RadioProfile`. This module hosts the
algorithm consumed by every backend.
"""

from __future__ import annotations

__all__ = ["interpolate_swr", "MeterType"]

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


def interpolate_swr(raw: int, meter_calibrations: dict[str, list[Any]] | None) -> float:
    """Convert raw SWR meter value (0-255) to a calibrated SWR ratio.

    Uses the ``swr`` calibration table from ``meter_calibrations`` (loaded
    from TOML's ``[[meters.swr.calibration]]`` blocks) when available,
    interpolating piecewise-linearly between points. Returns the legacy
    linear approximation ``1.0 + raw/255 * 8.9`` when no table is
    configured (preserves backward compat for rigs that don't yet ship
    calibration data).

    Mirrors ``yaesu_cat.radio._interpolate_swr`` so all backends share a
    single algorithm — the wfview piecewise-linear curve.
    """
    points = (meter_calibrations or {}).get("swr")
    if points:
        # Points are typically already sorted by raw, but sort defensively.
        sorted_pts = sorted(points, key=lambda p: p["raw"])
        if raw <= sorted_pts[0]["raw"]:
            return float(sorted_pts[0]["actual"])
        if raw >= sorted_pts[-1]["raw"]:
            return float(sorted_pts[-1]["actual"])
        for lo, hi in zip(sorted_pts, sorted_pts[1:]):
            if lo["raw"] <= raw <= hi["raw"]:
                span = hi["raw"] - lo["raw"]
                if span == 0:
                    return float(lo["actual"])
                t = (raw - lo["raw"]) / span
                return float(
                    float(lo["actual"])
                    + t * (float(hi["actual"]) - float(lo["actual"]))
                )
    # No table: legacy linear fallback (pre-#440 behavior).
    if raw <= 0:
        return 1.0
    return 1.0 + (raw / 255.0) * 8.9
