"""Radio profile and capability matrix for runtime routing and guards.

All profiles are loaded from TOML rig files in the ``rigs/`` directory.
There are **no** hardcoded profiles — adding a new radio means adding one
TOML file with zero Python changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Required, TypedDict

__all__ = [
    "ControlSpec",
    "FilterWidthSegment",
    "FilterWidthRule",
    "MeterCalibrationPoint",
    "RadioProfile",
    "RuleSpec",
    "get_radio_profile",
    "resolve_radio_profile",
    "KeyboardBinding",
    "KeyboardConfig",
]

logger = logging.getLogger(__name__)


class ControlSpec(TypedDict, total=False):
    """Specification for a single radio control (from TOML ``[controls.*]``)."""

    style: str
    raw_min: int
    raw_max: int
    raw_center: int
    display_min: int
    display_max: int
    display_unit: str


class MeterCalibrationPoint(TypedDict):
    """One calibration point for a meter (from TOML ``[[meters.*.calibration]]``)."""

    raw: int
    actual: float
    label: str


class RuleSpec(TypedDict, total=False):
    """Inter-control rule (from TOML ``[[rules]]``)."""

    kind: Required[str]
    fields: list[str]
    when_active: str
    disables: list[str]
    reason: str


def _normalize(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


@dataclass(frozen=True, slots=True)
class BandInfo:
    """Amateur band definition for UI band selector."""

    name: str
    start: int  # Hz
    end: int  # Hz
    default: int  # Hz — default tuning frequency
    bsr_code: int | None = None  # Band Stack Register code for CI-V 0x1A 0x01


@dataclass(frozen=True, slots=True)
class FreqRangeInfo:
    """Frequency range with optional band plan."""

    start: int  # Hz
    end: int  # Hz
    label: str
    bands: tuple[BandInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class FilterWidthSegment:
    """One linear segment of a filter-width index mapping."""

    hz_min: int
    hz_max: int
    step_hz: int
    index_min: int


@dataclass(frozen=True, slots=True)
class FilterWidthRule:
    """Per-mode filter-width behavior loaded from rig TOML."""

    defaults: tuple[int, ...] = ()
    fixed: bool = False
    step_hz: int | None = None
    min_hz: int | None = None
    max_hz: int | None = None
    segments: tuple[FilterWidthSegment, ...] = ()
    table: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class KeyboardBinding:
    """One keyboard shortcut binding loaded from rig TOML."""

    id: str
    action: str
    sequence: tuple[str, ...]
    section: str = "General"
    label: str | None = None
    description: str | None = None
    modifiers: tuple[str, ...] = ()
    repeatable: bool = False
    params: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class KeyboardConfig:
    """Keyboard shortcut configuration exposed to the web UI."""

    leader_key: str = "g"
    leader_timeout_ms: int = 1000
    alt_hints: bool = True
    help_title: str = "Keyboard Shortcuts"
    bindings: tuple[KeyboardBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class RadioProfile:
    """Runtime radio profile used by command routing and capability checks."""

    id: str
    model: str
    civ_addr: int
    receiver_count: int
    capabilities: frozenset[str]
    cmd29_routes: frozenset[tuple[int, int | None]]
    vfo_main_code: int | None = None
    vfo_sub_code: int | None = None
    # Explicit split (issue #710):
    #   *_ab_code       → VFO A↔B within the currently-selected receiver
    #   *_main_sub_code → MAIN↔SUB across receivers (dual-receiver rigs only)
    swap_ab_code: int | None = None
    equal_ab_code: int | None = None
    swap_main_sub_code: int | None = None
    equal_main_sub_code: int | None = None
    vfo_scheme: str = "main_sub"
    has_lan: bool = False
    freq_ranges: tuple[FreqRangeInfo, ...] = ()
    modes: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    filter_width_min: int = 50
    filter_width_max: int = 9999
    filter_width_encoding: str = "segmented_bcd_index"
    filter_config: dict[str, FilterWidthRule] | None = None
    att_values: tuple[int, ...] | None = None
    att_labels: dict[str, str] | None = None
    pre_values: tuple[int, ...] | None = None
    pre_labels: dict[str, str] | None = None
    agc_modes: tuple[int, ...] | None = None
    agc_labels: dict[str, str] | None = None
    data_mode_count: int = 0
    data_mode_labels: dict[str, str] | None = None
    protocol_type: str = "civ"
    controls: dict[str, ControlSpec] | None = None
    meter_calibrations: dict[str, list[MeterCalibrationPoint]] | None = None
    meter_redlines: dict[str, int] | None = None
    rules: tuple[RuleSpec, ...] = ()
    keyboard: KeyboardConfig | None = None
    antenna_tx_count: int = 1
    transceiver_count: int = 1
    scope_ref_min_db: float | None = None
    scope_ref_max_db: float | None = None
    scope_ref_step_db: float | None = None
    # Per-profile RX codec preference override (#797). When non-None, the first
    # entry is used as the initial ``audio_codec`` for radios created under this
    # profile (unless the caller passes an explicit non-default value). Values
    # are ``AudioCodec`` enum names (e.g. ``"PCM_1CH_16BIT"``).
    codec_preference: tuple[str, ...] | None = None

    @property
    def vfo_swap_code(self) -> int | None:
        """Legacy alias — prefers ``swap_main_sub_code`` for dual-RX rigs.

        Deprecated: use :attr:`swap_ab_code` or :attr:`swap_main_sub_code`
        directly (issue #710).
        """
        return self.swap_main_sub_code or self.swap_ab_code

    @property
    def vfo_equal_code(self) -> int | None:
        """Legacy alias — prefers ``equal_main_sub_code`` for dual-RX rigs.

        Deprecated: use :attr:`equal_ab_code` or :attr:`equal_main_sub_code`
        directly (issue #710).
        """
        return self.equal_main_sub_code or self.equal_ab_code

    def supports_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def supports_receiver(self, receiver: int) -> bool:
        return 0 <= receiver < self.receiver_count

    def supports_cmd29(self, command: int, sub: int | None = None) -> bool:
        return (command, sub) in self.cmd29_routes or (
            command,
            None,
        ) in self.cmd29_routes

    def resolve_filter_rule(
        self, mode: str | None, *, data_mode: int = 0
    ) -> FilterWidthRule | None:
        if not self.filter_config or not mode:
            return None
        base_mode = str(mode).upper()
        candidates: list[str] = []
        if data_mode > 0:
            candidates.append(f"{base_mode}-D")
        candidates.append(base_mode)
        if base_mode in {"USB", "LSB"}:
            if data_mode > 0:
                candidates.append("SSB-D")
            candidates.append("SSB")
        if base_mode == "CW-R":
            candidates.append("CW")
        if base_mode == "RTTY-R":
            candidates.append("RTTY")
        for key in candidates:
            rule = self.filter_config.get(key)
            if rule is not None:
                return rule
        return None


# ── TOML-driven profile registry ──────────────────────────────────

# Lazy-loaded on first access.  Populated from rigs/*.toml.
_profiles: dict[str, RadioProfile] | None = None
_by_normalized: dict[str, RadioProfile] = {}
_by_id: dict[str, RadioProfile] = {}
_by_civ_addr: dict[int, RadioProfile] = {}

# Search paths for rig TOML files (first existing directory wins).
_RIG_DIRS: list[Path] = [
    Path(__file__).resolve().parent.parent.parent / "rigs",  # dev: repo root/rigs/
    Path(__file__).resolve().parent / "rigs",  # installed: package/rigs/
]


def _ensure_loaded() -> dict[str, RadioProfile]:
    """Load TOML rig profiles on first access (lazy init)."""
    global _profiles, _by_normalized, _by_id, _by_civ_addr

    if _profiles is not None:
        return _profiles

    # Import here to avoid circular imports
    from .rig_loader import discover_rigs

    _profiles = {}
    _by_normalized = {}
    _by_id = {}
    _by_civ_addr = {}

    for rig_dir in _RIG_DIRS:
        if rig_dir.is_dir():
            rigs = discover_rigs(rig_dir)
            for model, rig_config in rigs.items():
                profile = rig_config.to_profile()
                _profiles[model] = profile
                _by_normalized[_normalize(model)] = profile
                _by_id[_normalize(profile.id)] = profile
                _by_civ_addr.setdefault(profile.civ_addr, profile)
            if rigs:
                logger.debug(
                    "Loaded %d rig profiles from %s: %s",
                    len(rigs),
                    rig_dir,
                    ", ".join(sorted(rigs.keys())),
                )
                break  # use first directory that has rigs

    if not _profiles:
        logger.warning(
            "No rig TOML profiles found in search paths: %s",
            [str(p) for p in _RIG_DIRS],
        )

    return _profiles


def get_radio_profile(name_or_id: str) -> RadioProfile:
    """Return a profile by model name or profile id."""
    _ensure_loaded()
    key = _normalize(name_or_id)
    profile = _by_id.get(key) or _by_normalized.get(key)
    if profile is None:
        known = ", ".join(sorted(_ensure_loaded().keys()))
        raise KeyError(f"Unknown radio profile {name_or_id!r}. Known models: {known}")
    return profile


def resolve_radio_profile(
    *,
    profile: RadioProfile | str | None = None,
    model: str | None = None,
    radio_addr: int | None = None,
) -> RadioProfile:
    """Resolve runtime profile from explicit profile/model or CI-V address."""
    _ensure_loaded()
    if isinstance(profile, RadioProfile):
        return profile
    if isinstance(profile, str) and profile.strip():
        return get_radio_profile(profile)
    if isinstance(model, str) and model.strip():
        return get_radio_profile(model)
    if radio_addr is not None and radio_addr in _by_civ_addr:
        return _by_civ_addr[radio_addr]
    # Default fallback — prefer IC-7610 (primary LAN reference rig), then any LAN profile
    profiles = _ensure_loaded()
    ic7610 = profiles.get("IC-7610")
    if ic7610 is not None and ic7610.has_lan:
        return ic7610
    for p in profiles.values():
        if p.has_lan:
            return p
    if profiles:
        return next(iter(profiles.values()))
    raise KeyError("No rig profiles loaded — check rigs/ directory")


def reload_profiles() -> None:
    """Force reload of TOML profiles (useful for tests)."""
    global _profiles
    _profiles = None
    _ensure_loaded()
