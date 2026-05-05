"""Band plan registry — load TOML files and serve via REST.

Loads all .toml files from the band-plans/ directory at startup,
indexes segments for fast frequency-range queries.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Sanitization ──

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MAX_STR_LEN = 200  # max length for label/notes/station fields
_VALID_MODES = frozenset(
    {"cw", "digital", "phone", "beacon", "broadcast", "utility", "military", "other"}
)


def _sanitize_str(val: Any, max_len: int = _MAX_STR_LEN) -> str | None:
    """Return a safe string or None. Strips HTML tags and control chars."""
    if val is None:
        return None
    s = str(val)[:max_len]
    # Strip HTML tags
    s = re.sub(r"<[^>]*>", "", s)
    # Strip control characters (keep printable + common whitespace)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    return s.strip() or None


def _sanitize_color(val: Any) -> str | None:
    """Return a valid hex color or None."""
    if not isinstance(val, str):
        return None
    return val if _HEX_COLOR_RE.match(val) else None


def _sanitize_freq(val: Any) -> int:
    """Return a non-negative integer frequency in Hz."""
    try:
        freq = int(val)
    except (TypeError, ValueError):
        return 0
    return max(0, min(freq, 500_000_000))  # 0 – 500 MHz


def _sanitize_opacity(val: Any, default: float = 0.20) -> float:
    """Return opacity clamped to 0.0–1.0."""
    try:
        o = float(val)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, o))


# Default mode colors (hex) — used when TOML doesn't specify a color
MODE_COLORS: dict[str, str] = {
    "cw": "#FF6A00",
    "digital": "#4ADE80",
    "phone": "#60A5FA",
    "beacon": "#FACC15",
    "broadcast": "#C084FC",
    "utility": "#F97316",
    "military": "#EF4444",
    "other": "#9CA3AF",
}

DEFAULT_OPACITY = 0.20


# Region → ham band plan file mapping
_REGION_HAM_FILE: dict[str, str] = {
    "US": "arrl-hf.toml",
    "IARU-R1": "iaru-r1.toml",
    "IARU-R2": "arrl-hf.toml",  # Region 2 = Americas, ARRL is primary
    "IARU-R3": "arrl-hf.toml",  # Fallback until R3 file is created
}


class BandPlanRegistry:
    """Load and query band plan data from TOML files."""

    def __init__(self, band_plans_dir: str | Path | None = None) -> None:
        self._dir = Path(band_plans_dir) if band_plans_dir else None
        self._layers: dict[str, dict[str, Any]] = {}  # layer_name -> meta
        self._segments: list[dict[str, Any]] = []  # flat list, sorted by start
        self._region: str = "US"

    @property
    def region(self) -> str:
        """Current active region."""
        return self._region

    def load(self, band_plans_dir: str | Path | None = None) -> None:
        """Load TOML files from the directory, respecting region config."""
        search_dir = Path(band_plans_dir) if band_plans_dir else self._dir
        if search_dir is None:
            logger.warning("band-plan: no directory configured")
            return

        if not search_dir.is_dir():
            logger.warning("band-plan: directory not found: %s", search_dir)
            return

        self._layers.clear()
        self._segments.clear()

        # Read config for region setting
        config_path = search_dir / "_config.toml"
        if config_path.is_file():
            try:
                with open(config_path, "rb") as f:
                    config = tomllib.load(f)
                self._region = config.get("settings", {}).get("region", "US")
            except Exception:
                logger.warning("band-plan: failed to load _config.toml, using US")
                self._region = "US"

        # Determine which ham file to load based on region
        ham_file = _REGION_HAM_FILE.get(self._region, "arrl-hf.toml")

        toml_files = sorted(search_dir.glob("*.toml"))
        if not toml_files:
            logger.info("band-plan: no TOML files in %s", search_dir)
            return

        for path in toml_files:
            if path.name.startswith("_"):
                continue  # skip config files
            # For ham layer files, only load the one matching the region
            if path.name in _REGION_HAM_FILE.values() and path.name != ham_file:
                logger.debug(
                    "band-plan: skipping %s (region=%s)", path.name, self._region
                )
                continue
            try:
                self._load_file(path)
            except Exception:
                logger.exception("band-plan: failed to load %s", path.name)

        # Sort all segments by start frequency
        self._segments.sort(key=lambda s: s["start"])
        logger.info(
            "band-plan: loaded %d segments from %d files (%d layers), region=%s",
            len(self._segments),
            len(self._layers),
            len(self._layers),
            self._region,
        )

    def _load_file(self, path: Path) -> None:
        """Parse a single TOML band plan file."""
        with open(path, "rb") as f:
            data = tomllib.load(f)

        meta = data.get("meta", {})
        layer = meta.get("layer", path.stem)
        priority = meta.get("priority", 0)

        self._layers[layer] = {
            "name": meta.get("name", path.stem),
            "layer": layer,
            "priority": priority,
            "source": meta.get("source", ""),
            "region": meta.get("region", ""),
            "updated": str(meta.get("updated", "")),
            "file": path.name,
        }

        bands = data.get("band", [])
        for band in bands:
            band_name = _sanitize_str(band.get("name", "?"), max_len=40) or "?"
            for seg in band.get("segment", []):
                start = _sanitize_freq(seg.get("start"))
                end = _sanitize_freq(seg.get("end"))
                if start == 0 and end == 0:
                    logger.warning(
                        "band-plan: skipping segment with no frequency in %s/%s",
                        path.name,
                        band_name,
                    )
                    continue
                if end < start:
                    start, end = end, start  # swap

                raw_mode = str(seg.get("mode", "other")).lower()
                mode = raw_mode if raw_mode in _VALID_MODES else "other"

                raw_color = _sanitize_color(seg.get("color"))
                color = raw_color or MODE_COLORS.get(mode, "#9CA3AF")

                self._segments.append(
                    {
                        "start": start,
                        "end": end,
                        "mode": mode,
                        "label": _sanitize_str(seg.get("label"), max_len=30)
                        or mode.upper(),
                        "color": color,
                        "opacity": _sanitize_opacity(
                            seg.get("opacity"), DEFAULT_OPACITY
                        ),
                        "band": band_name,
                        "layer": layer,
                        "priority": priority,
                        "url": _sanitize_str(seg.get("url"), max_len=500),
                        "notes": _sanitize_str(seg.get("notes")),
                        "station": _sanitize_str(seg.get("station"), max_len=80),
                        "language": _sanitize_str(seg.get("language"), max_len=20),
                        "schedule": _sanitize_str(seg.get("schedule"), max_len=100),
                        "license": _sanitize_str(seg.get("license"), max_len=40),
                    }
                )

    def get_segments(
        self,
        start_hz: int,
        end_hz: int,
        layers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return segments overlapping the given frequency range.

        Args:
            start_hz: Left edge of the visible spectrum (Hz).
            end_hz: Right edge of the visible spectrum (Hz).
            layers: Optional layer filter (None = all layers).

        Returns:
            List of segment dicts sorted by start frequency.
        """
        result: list[dict[str, Any]] = []
        for seg in self._segments:
            if seg["end"] < start_hz:
                continue
            if seg["start"] > end_hz:
                break  # sorted, no more matches
            if layers and seg["layer"] not in layers:
                continue
            result.append(seg)
        return result

    def get_layers(self) -> list[dict[str, Any]]:
        """Return all loaded layers with metadata."""
        return sorted(
            self._layers.values(),
            key=lambda layer: -layer.get("priority", 0),
        )

    @property
    def segment_count(self) -> int:
        """Total number of loaded segments."""
        return len(self._segments)
