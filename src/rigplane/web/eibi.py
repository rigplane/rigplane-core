"""EiBi broadcast station database — fetch, parse, cache, query.

Data source: http://www.eibispace.de/dx/
Format: semicolon-separated CSV, ~10K entries per season.
Free to use per EiBi license terms.
"""

from __future__ import annotations

import asyncio
import bisect
import csv
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# ── Constants ──

_EIBI_BASE = "http://www.eibispace.de/dx"
_CACHE_DIR_NAME = ".cache"
_CACHE_CSV = "eibi.csv"
_CACHE_META = "eibi-meta.json"
_FETCH_TIMEOUT = 30  # seconds
_CACHE_TTL = 7 * 86400  # 7 days

# Season naming: A=Summer (Mar-Oct), B=Winter (Oct-Mar), followed by 2-digit year
# Current file is always lowercase: sked-b25.csv

# ── Day parsing ──

_DAY_MAP = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
_DAY_DIGITS = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}

# ── Band classification (kHz ranges) ──

_BANDS: list[tuple[str, int, int]] = [
    ("LW", 148, 283),
    ("MW", 526, 1607),
    ("120m", 2300, 2495),
    ("90m", 3200, 3400),
    ("75m", 3900, 4000),
    ("60m", 4750, 5060),
    ("49m", 5900, 6200),
    ("41m", 7200, 7450),
    ("31m", 9400, 9900),
    ("25m", 11600, 12100),
    ("22m", 13570, 13870),
    ("19m", 15100, 15800),
    ("16m", 17480, 17900),
    ("15m", 18900, 19020),
    ("13m", 21450, 21850),
    ("11m", 25670, 26100),
]

# ── Language code → name (common ones) ──

_LANG_NAMES: dict[str, str] = {
    "A": "Arabic",
    "AB": "Abkhaz",
    "AF": "Afar",
    "AH": "Amharic",
    "AL": "Albanian",
    "AM": "Amoy",
    "AR": "Armenian",
    "AZ": "Azeri",
    "BE": "Bengali",
    "BG": "Bulgarian",
    "BM": "Burmese",
    "BR": "Brazilian",
    "BU": "Byelorussian",
    "C": "Chinese",
    "CA": "Cantonese",
    "CB": "Cham",
    "CR": "Creole",
    "CZ": "Czech",
    "D": "German",
    "DA": "Danish",
    "DI": "Dinka",
    "DR": "Dari",
    "DZ": "Dzongkha",
    "E": "English",
    "ES": "Estonian",
    "F": "French",
    "FA": "Faroese",
    "FI": "Finnish",
    "FJ": "Fijian",
    "FU": "Fulani",
    "G": "Greek",
    "GE": "Georgian",
    "HA": "Hausa",
    "HB": "Hebrew",
    "HI": "Hindi",
    "HU": "Hungarian",
    "I": "Italian",
    "IC": "Icelandic",
    "IN": "Indonesian",
    "J": "Japanese",
    "JV": "Javanese",
    "K": "Korean",
    "KA": "Kazakh",
    "KH": "Khmer",
    "KN": "Kannada",
    "KR": "Karen",
    "KU": "Kurdish",
    "KZ": "Kirghiz",
    "LA": "Lao",
    "LT": "Lithuanian",
    "LV": "Latvian",
    "M": "Mandarin",
    "ML": "Malay",
    "MN": "Mongolian",
    "MO": "Moldavian",
    "MY": "Malayalam",
    "NE": "Nepali",
    "NL": "Dutch",
    "NO": "Norwegian",
    "OR": "Oriya",
    "P": "Portuguese",
    "PA": "Pashto",
    "PJ": "Punjabi",
    "PL": "Polish",
    "R": "Russian",
    "RO": "Romanian",
    "S": "Spanish",
    "SC": "Serbo-Croat",
    "SD": "Sindhi",
    "SI": "Sinhalese",
    "SK": "Slovak",
    "SL": "Slovenian",
    "SO": "Somali",
    "SW": "Swahili",
    "T": "Thai",
    "TA": "Tamil",
    "TB": "Tibetan",
    "TG": "Tagalog",
    "TI": "Tigrinya",
    "TJ": "Tajik",
    "TK": "Turkmen",
    "TU": "Turkish",
    "TW": "Taiwanese",
    "UR": "Urdu",
    "UZ": "Uzbek",
    "VN": "Vietnamese",
    "W": "Wolof",
    "YO": "Yoruba",
    # Special codes
    "-CW": "Morse",
    "-EC": "Carrier",
    "-HF": "HFDL",
    "-MX": "Music",
    "-TS": "Time Signal",
    "-TY": "Digital/RTTY",
}


@dataclass
class EiBiStation:
    """Single EiBi broadcast entry."""

    freq_khz: float
    time_start: int  # UTC hour*100 + minute, e.g. 1430
    time_end: int
    days: str  # raw days string (empty = daily)
    country: str  # ITU code
    station: str
    language: str  # EiBi lang code
    target: str  # target area code
    remarks: str  # transmitter site / remarks
    persistence: int
    start_date: str
    end_date: str

    @property
    def freq_hz(self) -> int:
        """Frequency in Hz."""
        return int(self.freq_khz * 1000)

    @property
    def language_name(self) -> str:
        """Human-readable language name."""
        return _LANG_NAMES.get(self.language, self.language)

    @property
    def band(self) -> str:
        """Band name (e.g., '49m', 'MW')."""
        for name, lo, hi in _BANDS:
            if lo <= self.freq_khz <= hi:
                return name
        return "Other"

    def is_on_air(self, utc_now: datetime | None = None) -> bool:
        """Check if this station is broadcasting at the given UTC time."""
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)

        # Check day of week
        if self.days and not self._matches_day(utc_now):
            return False

        # Check time window
        now_hhmm = utc_now.hour * 100 + utc_now.minute
        if self.time_start <= self.time_end:
            # Normal: e.g. 0800-1600
            return self.time_start <= now_hhmm < self.time_end
        else:
            # Wraps midnight: e.g. 2200-0400
            return now_hhmm >= self.time_start or now_hhmm < self.time_end

    def _matches_day(self, utc_now: datetime) -> bool:
        """Check if current day matches the days field."""
        dow = utc_now.weekday()  # 0=Monday

        days = self.days.strip()
        if not days:
            return True  # daily

        # Special non-day codes
        if days in ("irr", "alt", "tent", "test", "harm", "imod", "Ram", "Haj"):
            return True  # show these always

        # Digit format: "1245" = Mon, Tue, Thu, Fri
        if days.isdigit():
            return str(dow + 1) in days

        # Range format: "Mo-Fr", "Mo-Sa"
        if "-" in days and len(days) == 5:
            parts = days.split("-")
            if len(parts) == 2 and parts[0] in _DAY_MAP and parts[1] in _DAY_MAP:
                start_d = _DAY_MAP[parts[0]]
                end_d = _DAY_MAP[parts[1]]
                if start_d <= end_d:
                    return start_d <= dow <= end_d
                else:
                    return dow >= start_d or dow <= end_d

        # List format: "MoWeFr"
        matched_any = False
        for abbr, idx in _DAY_MAP.items():
            if abbr in days:
                matched_any = True
                if idx == dow:
                    return True
        if matched_any:
            return False

        # Unknown format — assume daily
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON API."""
        return {
            "freq_khz": self.freq_khz,
            "freq_hz": self.freq_hz,
            "time_start": self.time_start,
            "time_end": self.time_end,
            "time_str": f"{self.time_start:04d}-{self.time_end:04d}",
            "days": self.days,
            "country": self.country,
            "station": self.station,
            "language": self.language,
            "language_name": self.language_name,
            "target": self.target,
            "remarks": self.remarks,
            "band": self.band,
            "on_air": self.is_on_air(),
        }

    def to_segment(self) -> dict[str, Any]:
        """Convert to band plan overlay segment format."""
        return {
            "start": self.freq_hz - 2500,  # ±2.5 kHz bandwidth
            "end": self.freq_hz + 2500,
            "mode": "broadcast",
            "label": self.station[:20],
            "color": "#C084FC",
            "opacity": 0.30 if self.is_on_air() else 0.10,
            "band": self.band,
            "layer": "broadcast-eibi",
            "priority": 5,
            "url": None,
            "notes": f"{self.language_name} → {self.target} ({self.days or 'daily'})",
            "station": self.station,
            "language": self.language_name,
            "schedule": f"{self.time_start:04d}-{self.time_end:04d} UTC",
            "license": None,
        }


def _download_url(url: str, ua: str = "icom-lan/1.0") -> bytes:
    """Blocking download helper (run in a thread)."""
    req = Request(url, headers={"User-Agent": ua})
    with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        data = resp.read()
        assert isinstance(data, bytes)
        return data


def _load_cache_files_sync(
    csv_path: Path, meta_path: Path
) -> tuple[bytes, dict[str, Any]]:
    """Synchronous helper for :meth:`EibiClient.load_cache`.

    Reads the CSV bytes and (optionally) the JSON meta sidecar in a
    single worker-thread hop, so the event loop is not blocked while
    the disk I/O completes. Returns an empty ``meta`` dict when the
    sidecar is absent — control flow matches the previous inline form.
    """
    raw = csv_path.read_bytes()
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        with open(meta_path) as f:
            meta = json.load(f)
    return raw, meta


# ── FCC AM/FM lookup ──

_FCC_AM_URL = "https://transition.fcc.gov/fcc-bin/amq"
_FCC_FM_URL = "https://transition.fcc.gov/fcc-bin/fmq"
# Cache: freq_khz → list of station dicts, TTL 24h
_fcc_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_FCC_CACHE_TTL = 86400


def _parse_fcc_pipe(raw: str) -> list[dict[str, Any]]:
    """Parse FCC pipe-delimited AM/FM query results."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()  # dedupe by callsign
    for line in raw.split("\n"):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 12:
            continue
        call = parts[1].strip()
        if not call or call in seen:
            continue
        seen.add(call)
        freq_str = parts[2].replace("kHz", "").strip()
        try:
            freq_khz = float(freq_str)
        except ValueError:
            continue
        city = parts[10].strip()
        state = parts[11].strip()
        owner = parts[26].strip() if len(parts) > 26 else ""
        power_str = parts[14].strip() if len(parts) > 14 else ""

        results.append(
            {
                "freq_khz": freq_khz,
                "freq_hz": int(freq_khz * 1000),
                "station": call,
                "city": city,
                "state": state,
                "country": "USA",
                "language": "E",
                "language_name": "English",
                "target": f"{city}, {state}",
                "remarks": f"{power_str} — {owner}" if owner else power_str,
                "band": "MW" if freq_khz < 1700 else "FM",
                "time_str": "local",
                "days": "",
                "on_air": True,
                "source": "FCC",
            }
        )
    return results


def _curl_download(url: str) -> str:
    """Download via curl subprocess (more reliable for FCC)."""
    import subprocess

    result = subprocess.run(
        [
            "curl",
            "-sL",
            "--max-time",
            "15",
            "-H",
            "User-Agent: Mozilla/5.0 (icom-lan)",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout


async def fcc_identify(freq_hz: int, tolerance_hz: int = 1000) -> list[dict[str, Any]]:
    """Look up US AM stations by frequency via FCC AM Query."""
    freq_khz = round(freq_hz / 1000)

    # Check cache
    if freq_khz in _fcc_cache:
        ts, cached = _fcc_cache[freq_khz]
        if time.time() - ts < _FCC_CACHE_TTL:
            return cached

    # Only query AM band (530-1700 kHz)
    if not (530 <= freq_khz <= 1700):
        return []

    url = (
        f"{_FCC_AM_URL}?call=&ession=&state=&city="
        f"&freq={freq_khz}&fre2={freq_khz}&type=1&list=4"
    )
    try:
        text = await asyncio.to_thread(_curl_download, url)
        results = _parse_fcc_pipe(text)
        _fcc_cache[freq_khz] = (time.time(), results)
        logger.info("fcc: found %d stations on %d kHz", len(results), freq_khz)
        return results
    except Exception as exc:
        logger.warning("fcc: lookup failed for %d kHz: %s", freq_khz, exc)
        return []


class EiBiProvider:
    """Fetch, parse, cache and query EiBi broadcast data."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._stations: list[EiBiStation] = []
        self._last_updated: str | None = None
        self._season: str | None = None
        self._loaded = False

        # Indexes for fast lookup
        self._by_freq: list[EiBiStation] = []  # sorted by freq_khz
        self._languages: set[str] = set()
        self._countries: set[str] = set()

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def station_count(self) -> int:
        return len(self._stations)

    @property
    def last_updated(self) -> str | None:
        return self._last_updated

    @property
    def season(self) -> str | None:
        return self._season

    def status(self) -> dict[str, Any]:
        """Return status info for REST API."""
        return {
            "loaded": self._loaded,
            "station_count": len(self._stations),
            "last_updated": self._last_updated,
            "season": self._season,
            "languages": sorted(self._languages),
            "countries": sorted(self._countries),
            "cache_fresh": self._is_cache_fresh(),
        }

    def _get_cache_dir(self) -> Path:
        """Resolve cache directory, creating if needed."""
        if self._cache_dir:
            d = self._cache_dir
        else:
            d = Path.home() / ".cache" / "icom-lan"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _is_cache_fresh(self) -> bool:
        """Check if cache exists and is within TTL."""
        cache_dir = self._get_cache_dir()
        meta_path = cache_dir / _CACHE_META
        if not meta_path.is_file():
            return False
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            ts = meta.get("fetched_at", 0)
            age_valid = (time.time() - ts) < _CACHE_TTL
            assert isinstance(age_valid, bool)
            return age_valid
        except Exception:
            return False

    def _current_season(self) -> str:
        """Determine current EiBi season code (e.g., 'b25')."""
        now = datetime.now(timezone.utc)
        year = now.year % 100
        # A = Summer (roughly late March – late October)
        # B = Winter (roughly late October – late March)
        if 3 <= now.month <= 10:
            return f"a{year:02d}"
        else:
            # Winter season uses the year it starts in
            if now.month <= 2:
                return f"b{year - 1:02d}"
            return f"b{year:02d}"

    async def fetch(self, force: bool = False) -> dict[str, Any]:
        """Download EiBi CSV from the internet, cache, and parse.

        Returns status dict with results.
        """
        if not force and self._is_cache_fresh():
            # Load from cache instead
            return await self.load_cache()

        season = self._current_season()
        url = f"{_EIBI_BASE}/sked-{season}.csv"
        logger.info("eibi: fetching %s", url)

        try:
            raw = await asyncio.to_thread(_download_url, url)
        except (URLError, OSError) as exc:
            logger.error("eibi: fetch failed: %s", exc)
            # Try fallback to previous season
            prev_season = self._previous_season(season)
            url2 = f"{_EIBI_BASE}/sked-{prev_season}.csv"
            logger.info("eibi: trying fallback %s", url2)
            try:
                raw = await asyncio.to_thread(_download_url, url2)
                season = prev_season
            except (URLError, OSError) as exc2:
                return {"error": str(exc2), "status": "fetch_failed"}

        # Cache to disk
        cache_dir = self._get_cache_dir()
        csv_path = cache_dir / _CACHE_CSV
        meta_path = cache_dir / _CACHE_META
        csv_path.write_bytes(raw)
        meta = {
            "season": season,
            "url": url,
            "fetched_at": time.time(),
            "fetched_iso": datetime.now(timezone.utc).isoformat(),
            "size_bytes": len(raw),
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        # Parse
        count = self._parse_csv(raw.decode("latin-1", errors="replace"))
        self._season = season
        fetched_iso = meta["fetched_iso"]
        assert isinstance(fetched_iso, str)
        self._last_updated = fetched_iso
        self._loaded = True

        logger.info("eibi: loaded %d stations from %s", count, season)
        return {
            "status": "ok",
            "season": season,
            "station_count": count,
            "languages": len(self._languages),
            "countries": len(self._countries),
        }

    async def load_cache(self) -> dict[str, Any]:
        """Load from disk cache without fetching."""
        cache_dir = self._get_cache_dir()
        csv_path = cache_dir / _CACHE_CSV
        meta_path = cache_dir / _CACHE_META

        if not csv_path.is_file():
            return {"status": "no_cache", "error": "No cached data found"}

        raw, meta = await asyncio.to_thread(_load_cache_files_sync, csv_path, meta_path)
        count = self._parse_csv(raw.decode("latin-1", errors="replace"))

        self._season = meta.get("season")
        self._last_updated = meta.get("fetched_iso")
        self._loaded = True

        logger.info("eibi: loaded %d stations from cache", count)
        return {
            "status": "ok",
            "season": self._season,
            "station_count": count,
            "source": "cache",
        }

    def _parse_csv(self, text: str) -> int:
        """Parse EiBi semicolon-separated CSV into station list."""
        self._stations.clear()
        self._languages.clear()
        self._countries.clear()

        reader = csv.reader(io.StringIO(text), delimiter=";")
        header_skipped = False

        for row in reader:
            if len(row) < 9:
                continue
            # Skip header row
            if not header_skipped:
                if row[0].startswith("kHz"):
                    header_skipped = True
                    continue
                header_skipped = True  # no header found, parse anyway

            try:
                freq_str = row[0].strip()
                if not freq_str:
                    continue
                freq_khz = float(freq_str)

                time_raw = row[1].strip()
                time_start, time_end = self._parse_time(time_raw)

                days = row[2].strip()
                country = row[3].strip()
                station = row[4].strip()
                language = row[5].strip()
                target = row[6].strip()
                remarks = row[7].strip() if len(row) > 7 else ""
                persistence = int(row[8].strip() or "0") if len(row) > 8 else 0
                start_date = row[9].strip() if len(row) > 9 else ""
                end_date = row[10].strip() if len(row) > 10 else ""

                # Skip inactive entries (persistence code 8)
                if persistence == 8:
                    continue

                entry = EiBiStation(
                    freq_khz=freq_khz,
                    time_start=time_start,
                    time_end=time_end,
                    days=days,
                    country=country,
                    station=station,
                    language=language,
                    target=target,
                    remarks=remarks,
                    persistence=persistence,
                    start_date=start_date,
                    end_date=end_date,
                )
                self._stations.append(entry)
                if language:
                    self._languages.add(language)
                if country:
                    self._countries.add(country)
            except (ValueError, IndexError):
                continue

        # Sort by frequency
        self._stations.sort(key=lambda s: s.freq_khz)
        self._by_freq = self._stations
        return len(self._stations)

    @staticmethod
    def _parse_time(raw: str) -> tuple[int, int]:
        """Parse '0830-1600' into (830, 1600)."""
        match = re.match(r"(\d{4})-(\d{4})", raw)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 0, 2400  # default: 24h

    @staticmethod
    def _previous_season(season: str) -> str:
        """Get previous EiBi season code."""
        letter = season[0]
        year = int(season[1:])
        if letter == "a":
            return f"b{year - 1:02d}"
        return f"a{year:02d}"

    # ── Query API ──

    def get_stations(
        self,
        on_air: bool = False,
        band: str | None = None,
        language: str | None = None,
        country: str | None = None,
        query: str | None = None,
        sort: str = "freq",
        page: int = 1,
        limit: int = 100,
        favourites: set[str] | None = None,
    ) -> dict[str, Any]:
        """Query stations with filters, pagination, and sorting."""
        utc_now = datetime.now(timezone.utc)
        results = self._stations

        # Filters
        if on_air:
            results = [s for s in results if s.is_on_air(utc_now)]
        if band:
            results = [s for s in results if s.band.lower() == band.lower()]
        if language:
            results = [s for s in results if s.language.lower() == language.lower()]
        if country:
            results = [s for s in results if s.country.lower() == country.lower()]
        if query:
            q = query.lower()
            results = [
                s
                for s in results
                if q in s.station.lower()
                or q in s.language_name.lower()
                or q in s.country.lower()
                or q in s.target.lower()
                or q in s.remarks.lower()
            ]

        total = len(results)

        # Sort
        if sort == "station":
            results.sort(key=lambda s: s.station.lower())
        elif sort == "country":
            results.sort(key=lambda s: s.country)
        elif sort == "language":
            results.sort(key=lambda s: s.language_name)
        elif sort == "on_air":
            results.sort(key=lambda s: (not s.is_on_air(utc_now), s.freq_khz))
        else:  # freq (default)
            results.sort(key=lambda s: s.freq_khz)

        # Paginate
        start = (page - 1) * limit
        page_results = results[start : start + limit]

        return {
            "stations": [s.to_dict() for s in page_results],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit if limit > 0 else 0,
        }

    def get_segments(
        self,
        start_hz: int,
        end_hz: int,
        on_air_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Return on-air stations as band plan overlay segments."""
        utc_now = datetime.now(timezone.utc)
        start_khz = start_hz / 1000
        end_khz = end_hz / 1000

        result: list[dict[str, Any]] = []
        lo = start_khz - 3
        hi = end_khz + 3
        start_idx = bisect.bisect_left(self._by_freq, lo, key=lambda s: s.freq_khz)
        for s in self._by_freq[start_idx:]:
            if s.freq_khz > hi:
                break
            if on_air_only and not s.is_on_air(utc_now):
                continue
            result.append(s.to_segment())
        return result

    def identify(self, freq_hz: int, tolerance_hz: int = 5000) -> list[dict[str, Any]]:
        """Identify what's broadcasting on/near a frequency right now.

        Returns on-air stations within tolerance, sorted by distance from freq.
        """
        if not self._loaded:
            return []

        utc_now = datetime.now(timezone.utc)
        freq_khz = freq_hz / 1000
        tol_khz = tolerance_hz / 1000

        candidates: list[tuple[float, EiBiStation]] = []
        lo = freq_khz - tol_khz
        hi = freq_khz + tol_khz
        start_idx = bisect.bisect_left(self._by_freq, lo, key=lambda s: s.freq_khz)
        for s in self._by_freq[start_idx:]:
            if s.freq_khz > hi:
                break
            if s.is_on_air(utc_now):
                dist = abs(s.freq_khz - freq_khz)
                candidates.append((dist, s))

        candidates.sort(key=lambda x: x[0])
        return [s.to_dict() for _, s in candidates[:5]]

    def get_bands(self) -> list[dict[str, Any]]:
        """Return list of bands with station counts."""
        from collections import Counter

        band_counts: Counter[str] = Counter()
        for s in self._stations:
            band_counts[s.band] += 1

        utc_now = datetime.now(timezone.utc)
        on_air_counts: Counter[str] = Counter()
        for s in self._stations:
            if s.is_on_air(utc_now):
                on_air_counts[s.band] += 1

        return [
            {
                "band": name,
                "total": band_counts.get(name, 0),
                "on_air": on_air_counts.get(name, 0),
            }
            for name, _, _ in _BANDS
            if band_counts.get(name, 0) > 0
        ]
