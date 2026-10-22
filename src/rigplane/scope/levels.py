"""Adaptive dB→pixel level mapping for scope displays.

Extracted from ``audio/fft_scope.py`` (MOR-3152) so every spectrum consumer
— the audio FFT panadapter today, other dB-domain spectra tomorrow — shares
one tuned adaptive level mapper instead of each keeping a private copy.

The mapping window is ADAPTIVE (MOR-512): instead of a fixed dB window, a
per-stream noise floor and signal ceiling are tracked and the display window
slides to follow them, so any radio/level stays visible.

Usage::

    from rigplane.scope.levels import AdaptiveLevelMapper

    # Full-spectrum estimation, defaults equal the audio FFT constants.
    mapper = AdaptiveLevelMapper()

    # Estimate over an in-band bin slice only (e.g. the audio baseband).
    mapper = AdaptiveLevelMapper(inband_bins=(2, 149))

    pixels: bytes = mapper.map(db)  # db: per-bin dB array → 0..pixel_max bytes

Requires numpy (core dependency; lazy-imported to keep module import light).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rigplane.core._optional_deps import _require_numpy

if TYPE_CHECKING:
    import numpy as np

__all__ = ["AdaptiveLevelMapper"]


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid hard dependency at module level."""
    _require_numpy()
    import numpy as np

    return np


class AdaptiveLevelMapper:
    """Adaptive dB→pixel level mapper with tracked floor/ceil (MOR-512).

    Tracks a robust noise floor (low percentile) and signal ceiling (high
    percentile) of the (optionally in-band-restricted) dB array, smooths each
    with an EMA / attack-decay so the window neither jitters nor pumps, then
    applies margins, a minimum span (so silence does not amplify noise) and
    absolute clamps. ``db_floor`` maps to pixel 0, ``db_ceil`` to
    ``pixel_max``.

    Args:
        floor_pct: Percentile of the estimation region tracking the floor.
        ceil_pct: Percentile of the estimation region tracking the ceil.
        floor_alpha: Per-frame EMA blend factor for the floor (slow = stable).
        ceil_attack: Per-frame blend factor when the observed ceil rises.
        ceil_decay: Per-frame blend factor when the observed ceil falls
            (slow decay avoids pumping when a signal disappears).
        pixel_max: Top of the pixel range (ScopeFrame convention: 160).
        inband_bins: Optional ``(lo, hi)`` inclusive bin-index slice the
            floor/ceil percentiles are estimated over (the bins that carry
            the signal the display shows). ``None`` estimates over the full
            array. A degenerate range (e.g. wider than the array) falls back
            to the full array.
        floor_margin_db: Margin placed below the tracked floor (dB).
        ceil_margin_db: Margin placed above the tracked ceil (dB).
        min_span_db: Minimum display-window span (dB).
        abs_floor_min_db: Absolute lower clamp for the floor (dB).
        abs_floor_max_db: Absolute upper clamp for the floor (dB).
        abs_ceil_max_db: Absolute upper clamp for the ceil (dB).
    """

    def __init__(
        self,
        *,
        floor_pct: float = 30.0,
        ceil_pct: float = 99.0,
        floor_alpha: float = 0.05,
        ceil_attack: float = 0.30,
        ceil_decay: float = 0.05,
        pixel_max: int = 160,
        inband_bins: tuple[int, int] | None = None,
        floor_margin_db: float = 5.0,
        ceil_margin_db: float = 3.0,
        min_span_db: float = 45.0,
        abs_floor_min_db: float = -160.0,
        abs_floor_max_db: float = -20.0,
        abs_ceil_max_db: float = 0.0,
    ) -> None:
        self._np = _import_numpy()

        self._floor_pct = floor_pct
        self._ceil_pct = ceil_pct
        self._floor_alpha = floor_alpha
        self._ceil_attack = ceil_attack
        self._ceil_decay = ceil_decay
        self._pixel_max = pixel_max
        self._inband_bins = inband_bins

        self._floor_margin_db = floor_margin_db
        self._ceil_margin_db = ceil_margin_db
        self._min_span_db = min_span_db
        self._abs_floor_min_db = abs_floor_min_db
        self._abs_floor_max_db = abs_floor_max_db
        self._abs_ceil_max_db = abs_ceil_max_db

        # Tracked window state. None until seeded from the first processed
        # frame, then EMA/attack-decay-tracked toward the live floor/ceil.
        self._floor_ema: float | None = None
        self._ceil_ema: float | None = None

    def set_inband_bins(self, lo: int, hi: int) -> None:
        """Update the in-band bin slice used for floor/ceil estimation.

        Args:
            lo: First bin index (inclusive).
            hi: Last bin index (inclusive).
        """
        self._inband_bins = (lo, hi)

    def reset(self) -> None:
        """Clear the tracked floor/ceil state.

        The next frame re-seeds the window, exactly as on construction.
        """
        self._floor_ema = None
        self._ceil_ema = None

    def _estimation_slice(self, db: Any) -> Any:
        """Return the dB slice the floor/ceil percentiles are estimated over."""
        if self._inband_bins is None:
            return db
        lo, hi = self._inband_bins
        hi = min(hi, len(db) - 1)
        if hi <= lo:
            return db
        return db[lo : hi + 1]

    def window(self, db: "np.ndarray") -> tuple[float, float]:
        """Adapt the tracked window to ``db`` and return the display window.

        Args:
            db: The per-bin dB array for this frame.

        Returns:
            ``(db_floor, db_ceil)`` — the window endpoints to map to pixels.
        """
        np = self._np

        estimation_db = self._estimation_slice(db)
        obs_floor = float(np.percentile(estimation_db, self._floor_pct))
        obs_ceil = float(np.percentile(estimation_db, self._ceil_pct))

        if self._floor_ema is None or self._ceil_ema is None:
            # Seed from the first frame so startup is immediately sane.
            self._floor_ema = obs_floor
            self._ceil_ema = obs_ceil
        else:
            # Floor: slow EMA → stable baseline.
            self._floor_ema += (obs_floor - self._floor_ema) * self._floor_alpha
            # Ceil: fast attack up, slow decay down → no pump when signal drops.
            ceil_alpha = (
                self._ceil_attack if obs_ceil > self._ceil_ema else self._ceil_decay
            )
            self._ceil_ema += (obs_ceil - self._ceil_ema) * ceil_alpha

        # Apply display margins.
        db_floor = self._floor_ema - self._floor_margin_db
        db_ceil = self._ceil_ema + self._ceil_margin_db

        # Absolute sanity clamps.
        db_floor = min(max(db_floor, self._abs_floor_min_db), self._abs_floor_max_db)
        db_ceil = min(db_ceil, self._abs_ceil_max_db)

        # Minimum span: never let the window collapse onto a flat noise field
        # (silence) — keep noise pinned near the bottom.
        if db_ceil - db_floor < self._min_span_db:
            db_ceil = db_floor + self._min_span_db

        return db_floor, db_ceil

    def map(self, db: "np.ndarray") -> bytes:
        """Map a per-bin dB array to pixel bytes through the adaptive window.

        Linear mapping: ``db_floor`` → 0, ``db_ceil`` → ``pixel_max``, clipped
        to ``[0, pixel_max]``. Adapts the tracked window first (see
        :meth:`window`).

        Args:
            db: The per-bin dB array for this frame.

        Returns:
            One pixel byte per input bin (``uint8``, 0..pixel_max).
        """
        np = self._np

        db_floor, db_ceil = self.window(db)
        db_range = db_ceil - db_floor
        pixels_float = (db - db_floor) / db_range * self._pixel_max
        pixels_uint8 = np.clip(pixels_float, 0, self._pixel_max).astype(np.uint8)
        return bytes(pixels_uint8)
