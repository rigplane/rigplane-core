"""Shared radio state cache.

Holds the last-known radio state fields with monotonic timestamps so
callers can decide whether a cached value is still fresh enough to use
without issuing another CI-V round-trip.

Not thread-safe by design: all access must happen on the same asyncio
event loop (single-loop model).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

__all__ = ["CacheField", "StateCache"]

# Literal union of all cacheable field names (used for is_fresh).
CacheField = Literal[
    "freq",
    "mode",
    "vfo",
    "ptt",
    "s_meter",
    "rf_power",
    "data_mode",
    "powerstat",
    "swr",
    "alc",
    "rf_gain",
    "af_level",
    "attenuator",
    "preamp",
]


@dataclass(slots=True)
class StateCache:
    """Last-known radio state with per-field monotonic timestamps.

    Each logical value has a corresponding ``<field>_ts`` timestamp
    (``time.monotonic()``).  A timestamp of ``0.0`` means the field has
    never been written.

    Attributes:
        freq: Last-known VFO-A frequency in Hz.
        freq_ts: Monotonic timestamp of the last ``freq`` update.
        mode: Last-known mode as a hamlib mode string (e.g. ``"USB"``).
        filter_width: Last-known IC-7610 filter number (1–3) or ``None``.
        mode_ts: Monotonic timestamp of the last mode update.
        vfo: Current VFO name (always ``"VFOA"`` for now).
        vfo_ts: Monotonic timestamp of the last VFO update.
        ptt: Last-known PTT state.
        ptt_ts: Monotonic timestamp of the last PTT update.
        s_meter: Last-known raw S-meter value (0–241) or ``None``.
        s_meter_ts: Monotonic timestamp of the last S-meter update.
        rf_power: Last-known normalised RF power (0.0–1.0) or ``None``.
        rf_power_ts: Monotonic timestamp of the last RF-power update.
        data_mode: Last-known IC-7610 DATA mode state (True = DATA1 active).
        data_mode_ts: Monotonic timestamp of the last data_mode update.
    """

    # Frequency
    freq: int = 0
    freq_ts: float = 0.0

    # Mode / filter
    mode: str = "USB"
    filter_width: int | None = None
    mode_ts: float = 0.0

    # VFO
    vfo: str = "VFOA"
    vfo_ts: float = 0.0

    # PTT
    ptt: bool = False
    ptt_ts: float = 0.0

    # S-meter
    s_meter: int | None = None
    s_meter_ts: float = 0.0

    # RF power (normalised 0.0–1.0)
    rf_power: float | None = None
    rf_power_ts: float = 0.0

    # DATA mode (IC-7610 0x1A 0x06)
    data_mode: bool = False
    data_mode_ts: float = 0.0

    # Power status (on/off)
    powerstat: bool = True
    powerstat_ts: float = 0.0

    # SWR (calibrated ratio, >= 1.0; populated from MetersCapable.get_swr)
    swr: float | None = None
    swr_ts: float = 0.0

    # ALC (raw 0–255)
    alc: float | None = None
    alc_ts: float = 0.0

    # RF gain (0–255)
    rf_gain: float | None = None
    rf_gain_ts: float = 0.0

    # AF level (0–255)
    af_level: float | None = None
    af_level_ts: float = 0.0

    # Attenuator (dB: 0, 3, 6, … 45)
    attenuator: int | None = None
    attenuator_ts: float = 0.0

    # Preamp (0=off, 1=PREAMP1, 2=PREAMP2)
    preamp: int | None = None
    preamp_ts: float = 0.0

    # Dual watch (IC-7610 0x07 0xC2)
    dual_watch: bool = False

    # ------------------------------------------------------------------
    # Freshness check
    # ------------------------------------------------------------------

    def is_fresh(self, field: CacheField, max_age_s: float) -> bool:
        """Return True if *field* was updated within *max_age_s* seconds.

        Args:
            field: Cache field name to check.
            max_age_s: Maximum acceptable age in seconds.

        Returns:
            ``True`` when the field has a timestamp and the elapsed time
            since that timestamp is strictly less than *max_age_s*.
        """
        ts: float
        match field:
            case "freq":
                ts = self.freq_ts
            case "mode":
                ts = self.mode_ts
            case "vfo":
                ts = self.vfo_ts
            case "ptt":
                ts = self.ptt_ts
            case "s_meter":
                ts = self.s_meter_ts
            case "rf_power":
                ts = self.rf_power_ts
            case "data_mode":
                ts = self.data_mode_ts
            case "powerstat":
                ts = self.powerstat_ts
            case "swr":
                ts = self.swr_ts
            case "alc":
                ts = self.alc_ts
            case "rf_gain":
                ts = self.rf_gain_ts
            case "af_level":
                ts = self.af_level_ts
            case "attenuator":
                ts = self.attenuator_ts
            case "preamp":
                ts = self.preamp_ts
            case _:  # pragma: no cover
                return False
        if ts == 0.0:
            return False
        return (time.monotonic() - ts) < max_age_s

    # ------------------------------------------------------------------
    # Update helpers
    # ------------------------------------------------------------------

    def update_freq(self, freq: int) -> None:
        """Store a new frequency value and record the current timestamp."""
        self.freq = freq
        self.freq_ts = time.monotonic()

    def invalidate_freq(self) -> None:
        """Mark the frequency as stale (forces the next read to hit radio)."""
        self.freq_ts = 0.0

    def update_mode(self, mode: str, filter_width: int | None) -> None:
        """Store a new mode/filter value and record the current timestamp.

        Args:
            mode: Hamlib mode string (e.g. ``"USB"``, ``"CW"``).
            filter_width: IC-7610 filter number (1–3) or ``None``.
        """
        self.mode = mode
        self.filter_width = filter_width
        self.mode_ts = time.monotonic()

    def invalidate_mode(self) -> None:
        """Mark the mode as stale (forces the next read to hit radio)."""
        self.mode_ts = 0.0

    def update_ptt(self, ptt: bool) -> None:
        """Store a new PTT state and record the current timestamp."""
        self.ptt = ptt
        self.ptt_ts = time.monotonic()

    def update_s_meter(self, value: int) -> None:
        """Store a new raw S-meter value and record the current timestamp."""
        self.s_meter = value
        self.s_meter_ts = time.monotonic()

    def update_rf_power(self, value: float) -> None:
        """Store a new normalised RF-power value and record the current timestamp."""
        self.rf_power = value
        self.rf_power_ts = time.monotonic()

    def update_data_mode(self, on: bool) -> None:
        """Store a new DATA mode state and record the current timestamp."""
        self.data_mode = on
        self.data_mode_ts = time.monotonic()

    def invalidate_data_mode(self) -> None:
        """Mark DATA mode as stale (forces the next read to hit radio)."""
        self.data_mode_ts = 0.0

    def update_powerstat(self, on: bool) -> None:
        """Store a new power status and record the current timestamp."""
        self.powerstat = on
        self.powerstat_ts = time.monotonic()

    def invalidate_powerstat(self) -> None:
        """Mark power status as stale (forces the next read to hit radio)."""
        self.powerstat_ts = 0.0

    def update_swr(self, value: float) -> None:
        """Store a new SWR meter value and record the current timestamp."""
        self.swr = value
        self.swr_ts = time.monotonic()

    def update_alc(self, value: float) -> None:
        """Store a new ALC meter value and record the current timestamp."""
        self.alc = value
        self.alc_ts = time.monotonic()

    def update_rf_gain(self, value: float) -> None:
        """Store a new RF gain value and record the current timestamp."""
        self.rf_gain = value
        self.rf_gain_ts = time.monotonic()

    def update_af_level(self, value: float) -> None:
        """Store a new AF level value and record the current timestamp."""
        self.af_level = value
        self.af_level_ts = time.monotonic()

    def update_attenuator(self, value: int) -> None:
        """Store a new attenuator dB value and record the current timestamp."""
        self.attenuator = value
        self.attenuator_ts = time.monotonic()

    def update_preamp(self, value: int) -> None:
        """Store a new preamp level and record the current timestamp."""
        self.preamp = value
        self.preamp_ts = time.monotonic()

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Return a dict of current values and their ages in seconds.

        Age keys are ``<field>_age``.  The age is ``None`` if the field
        has never been written (timestamp is 0.0), otherwise it is a
        non-negative float.

        Returns:
            Dictionary with all cached fields and their ages.
        """
        now = time.monotonic()

        def _age(ts: float) -> float | None:
            return (now - ts) if ts > 0.0 else None

        return {
            "freq": self.freq,
            "freq_age": _age(self.freq_ts),
            "mode": self.mode,
            "filter_width": self.filter_width,
            "mode_age": _age(self.mode_ts),
            "vfo": self.vfo,
            "vfo_age": _age(self.vfo_ts),
            "ptt": self.ptt,
            "ptt_age": _age(self.ptt_ts),
            "s_meter": self.s_meter,
            "s_meter_age": _age(self.s_meter_ts),
            "rf_power": self.rf_power,
            "rf_power_age": _age(self.rf_power_ts),
            "data_mode": self.data_mode,
            "data_mode_age": _age(self.data_mode_ts),
            "swr": self.swr,
            "swr_age": _age(self.swr_ts),
            "alc": self.alc,
            "alc_age": _age(self.alc_ts),
            "rf_gain": self.rf_gain,
            "rf_gain_age": _age(self.rf_gain_ts),
            "af_level": self.af_level,
            "af_level_age": _age(self.af_level_ts),
            "attenuator": self.attenuator,
            "attenuator_age": _age(self.attenuator_ts),
            "preamp": self.preamp,
            "preamp_age": _age(self.preamp_ts),
        }
