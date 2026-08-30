"""Radio profile and capability matrix for runtime routing and guards.

All profiles are loaded from TOML rig files in the ``rigs/`` directory.
There are **no** hardcoded profiles — adding a new radio means adding one
TOML file with zero Python changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Never, NotRequired, Required, TypedDict

from rigplane.commands.command_map import CommandMap
from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.tx_interlock_contract import (
    TxInterlockCommandFamily,
    TxInterlockDisposition,
)

__all__ = [
    "ControlLookupPoint",
    "EncodedControlChoice",
    "ControlDomainSpec",
    "ControlSpec",
    "FilterWidthSegment",
    "FilterWidthRule",
    "MeterCalibrationPoint",
    "RadioProfile",
    "RuleSpec",
    "TxPolicy",
    "get_radio_profile",
    "resolve_radio_profile",
    "KeyboardBinding",
    "KeyboardConfig",
]

logger = logging.getLogger(__name__)


class ControlLookupPoint(TypedDict):
    """One normalized lookup point for a profile control domain."""

    raw: int
    display: str


class EncodedControlLabelChoice(TypedDict):
    """One encoded choice represented by a truthful non-numeric label."""

    raw: int
    label: str


class EncodedControlNumericChoice(TypedDict):
    """One encoded choice represented by an exact numeric display value."""

    raw: int
    display: str


EncodedControlChoice = EncodedControlLabelChoice | EncodedControlNumericChoice


class ControlSpec(TypedDict, total=False):
    """Legacy control specification (from TOML ``[controls.*]``).

    This remains callable for consumers that construct legacy controls at
    runtime. Explicit scalar domains use ``ControlDomainSpec`` instead.
    """

    style: str
    raw_min: int
    raw_max: int
    raw_center: int
    display_min: int
    display_max: int
    display_unit: str


class _ControlDomainBase(TypedDict):
    """Fields shared by all normalized public control domains."""

    raw_min: int
    raw_max: int
    raw_step: int
    raw_origin: int
    display_min: str
    display_max: str
    display_step: str
    display_origin: str
    display_unit: str
    quantization: str
    restoration: str
    style: NotRequired[str]


class IdentityControlDomainSpec(_ControlDomainBase):
    mapping: Required[Literal["identity"]]
    raw_center: NotRequired[Never]
    display_center: NotRequired[Never]
    lookup: NotRequired[Never]


class LinearControlDomainSpec(_ControlDomainBase):
    mapping: Required[Literal["linear"]]
    raw_center: NotRequired[Never]
    display_center: NotRequired[Never]
    lookup: NotRequired[Never]


class CenteredControlDomainSpec(_ControlDomainBase):
    mapping: Required[Literal["centered"]]
    raw_center: int
    display_center: str
    lookup: NotRequired[Never]


class LookupControlDomainSpec(_ControlDomainBase):
    mapping: Required[Literal["lookup"]]
    lookup: list[ControlLookupPoint]
    raw_center: NotRequired[Never]
    display_center: NotRequired[Never]


class EncodedControlDomainSpec(TypedDict):
    """Discrete raw codes whose choices may be labeled or numeric."""

    mapping: Required[Literal["encoded"]]
    choices: list[EncodedControlChoice]
    style: NotRequired[str]


ControlDomainSpec = (
    IdentityControlDomainSpec
    | LinearControlDomainSpec
    | CenteredControlDomainSpec
    | LookupControlDomainSpec
    | EncodedControlDomainSpec
)


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
class TxPolicy:
    """Measured per-radio transmit policy (MOR-1912, ADR row 3a).

    Both fields carry only bench-measured facts — no speculative hooks.

    ``refused_during_tx`` names the command families this radio refuses on
    its own while transmitting. Entries are opaque, validated strings: the
    single source of truth for the family vocabulary is
    ``core/tx_authority.py`` (landing separately), so this type does not
    check membership, only shape.

    ``tx_state_map`` is the positive transmit-state map for the radio's PTT
    read-back: it lists the raw values that mean the radio is receiving.
    Everything else — including a raw value with no entry at all — must be
    treated as *not receiving* (§3.7 of the transmit-authority ADR). Use
    :meth:`is_receiving` rather than testing the map directly so that rule
    cannot be quietly inverted by a later reader.
    """

    refused_during_tx: frozenset[str] = frozenset()
    tx_state_map: dict[str, str] = field(default_factory=dict)

    def is_receiving(self, raw_value: str) -> bool:
        """True only if ``raw_value`` is explicitly mapped to receiving.

        An unmapped value — including one that simply isn't in the map —
        is never receiving. This is the fail-closed direction the positive
        transmit-state map exists to guarantee.
        """
        return self.tx_state_map.get(raw_value) == "rx"

    def attribution(self, raw_value: str) -> str | None:
        """The vendor label mapped to ``raw_value``, or ``None`` if unmapped.

        Display-grade only (MOR-1941, §3.7 of the transmit-authority ADR):
        unlike :meth:`is_receiving`, this carries no fail-closed safety
        rule — an unmapped value is simply ``None``, not a hazard answer.
        Yaesu's three-valued ``TX;`` answer surfaces here as ``"rx"`` /
        ``"tx_cat"`` / ``"tx_other"``; a vendor with no attribution (e.g.
        Icom) never populates ``tx_state_map`` with more than ``"rx"``, so
        this is honestly ``None`` for every other raw value.
        """
        return self.tx_state_map.get(raw_value)


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
    # How provider readbacks identify VFO state. ``selected_unselected``
    # exposes relative radio facts without claiming absolute A/B identity.
    vfo_readback: str = "none"
    has_lan: bool = False
    freq_ranges: tuple[FreqRangeInfo, ...] = ()
    modes: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    command_names: frozenset[str] = frozenset()
    # Command names this radio is confirmed NOT to have, per a named source
    # recorded in the TOML entry itself (MOR-2005 step 4a: the
    # `{ absent = "<source>" }` spelling in `[commands]`, parsed into
    # `commands/command_spec.py: AbsentCommandSpec`). Disjoint from
    # ``command_names`` by construction (`profiles/rig_loader.py:
    # RigConfig.to_profile` partitions ``self.commands`` between the two).
    # A name in neither set is the third, still-unrepresented-by-a-refusal-
    # policy state: "nobody has looked" — see ``supports_command``'s
    # docstring for what does and does not yet distinguish it from this set.
    absent_command_names: frozenset[str] = frozenset()
    # Source text for each ``absent_command_names`` entry (MOR-2005 step 4b,
    # plan §4 Step 4 / §8.1 D1 state 2): ``absent_command_names`` alone does
    # not carry D2's required provenance
    # (`commands/command_spec.py: AbsentCommandSpec.source`) -- step 4a's
    # ``to_profile`` partition kept the name and dropped the source. Keyed
    # the same as ``absent_command_names``, this lets the runtime refusal
    # for state 2 (`commands/bound.py: BoundCommands`) quote "per <source>"
    # without `commands/` importing anything from `profiles`: the caller
    # (`runtime/radio.py: CoreRadio.__init__`) reads this plain
    # ``dict[str, str]`` and passes it down as data. See
    # `profiles/rig_loader.py: RigConfig.to_profile`.
    absent_command_sources: dict[str, str] = field(default_factory=dict)
    # The radio's CI-V wire bytes by command name (MOR-2003 Step 3). ``None``
    # means this is a hand-built ``RadioProfile`` constructed outside
    # ``profiles/rig_loader.py`` -- e.g. directly in a test -- with no map
    # supplied at all. An empty, non-``None`` ``CommandMap`` means the
    # opposite: a profile that *was* loaded and declares no CI-V commands
    # (a CAT-only rig, whose TOML entries are all
    # ``CatCommandSpec`` and get dropped by
    # ``RigConfig.to_command_map`` -- a known, recorded asymmetry, plan
    # §8.1 Q8, not resolved here). `runtime/radio.py: CoreRadio.__init__`
    # treats both the same way: it binds an empty `CommandMap` rather than
    # raising.
    command_map: CommandMap | None = None
    filter_width_min: int = 50
    filter_width_max: int = 9999
    filter_width_encoding: str = "segmented_bcd_index"
    filter_config: dict[str, FilterWidthRule] | None = None
    max_watts: int | None = None
    att_values: tuple[int, ...] | None = None
    att_labels: dict[str, str] | None = None
    pre_values: tuple[int, ...] | None = None
    pre_labels: dict[str, str] | None = None
    agc_modes: tuple[int, ...] | None = None
    agc_labels: dict[str, str] | None = None
    # MOR-1534: enumerated-domain controls that were declared in TOML but
    # never parsed/validated (break_in, notch width) or hardcoded via an
    # IC-7610-specific enum with no profile domain to check against
    # (ssb_tx_bw, filter_shape). ``None`` means the profile declared no
    # domain — see each ``CoreRadio`` setter's docstring for what that means
    # for that specific control (some fail loud, some are permissive).
    break_in_modes: tuple[int, ...] | None = None
    break_in_labels: dict[str, str] | None = None
    notch_width_values: tuple[int, ...] | None = None
    notch_width_labels: dict[str, str] | None = None
    ssb_tx_bw_values: tuple[int, ...] | None = None
    ssb_tx_bw_labels: dict[str, str] | None = None
    filter_shape_values: tuple[int, ...] | None = None
    filter_shape_labels: dict[str, str] | None = None
    # MOR-1447 leg 2: "separate" (default, two independent controls) or
    # "combined" (Icom-style single RF/SQL knob). Data-driven from
    # ``[capabilities].rf_sql_control_model`` in the rig TOML — never a
    # vendor/model-name branch in code.
    rf_sql_control_model: str = "separate"
    data_mode_count: int = 0
    data_mode_labels: dict[str, str] | None = None
    # When True, MAIN set_mode routes through CI-V 0x26 0x00 (set selected
    # receiver mode) instead of the bare 0x06. Data-driven: derived from the
    # profile declaring a ``set_selected_mode`` command (e.g. Xiegu X6200,
    # whose 0x06 mode-set is a hardware-confirmed no-op). Default False keeps
    # the unchanged 0x06 path for every rig that does not declare it.
    set_mode_via_selected: bool = False
    protocol_type: str = "civ"
    # Hamlib rig_model integer (from rigs_list.h). Used by the validate
    # ``--provider hamlib`` path to launch stock rigctld with ``-m <id>``.
    hamlib_model_id: int = 2028
    controls: dict[str, ControlSpec | ControlDomainSpec] | None = None
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
    tx_codec: str | None = None
    default_sample_rate_hz: int | None = None
    supported_sample_rates_hz: tuple[int, ...] | None = None
    sample_rate_by_codec: dict[str, int] | None = None
    browser_rx_transport: str | None = None
    browser_rx_transcode_to_opus: bool | None = None
    # Capability names whose validate checks route through the set-and-observe
    # engine path (no read-first) instead of read-modify-verify-restore. Data-
    # driven from ``[validation].write_only_controls`` in the rig TOML (MOR-208).
    # Empty by default: every control uses the standard RMVR path.
    write_only_controls: frozenset[str] = frozenset()
    # Provider-specific state acquisition metadata (MOR-344). This is profile
    # data only; future schedulers/adapters consume it instead of Web or
    # rigctld delivery code branching on radio model.
    state_acquisition: RadioAcquisitionProfile | None = None
    tx_interlock_disposition_overrides: dict[
        TxInterlockCommandFamily, TxInterlockDisposition
    ] = field(default_factory=dict)
    # Measured per-radio transmit policy (MOR-1912). Parsed and carried
    # here; nothing reads it yet — the transmit-authority engine that will
    # consume it lands in a later row of the same epic.
    tx_policy: TxPolicy = field(default_factory=TxPolicy)

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

    def supports_command(self, command_name: str) -> bool:
        """Whether ``command_name`` is declared (with bytes or a CAT
        template) by this profile.

        Plan `docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1
        D1 names three states for a command name: (1) declared — this
        returns True; (2) declared absent, via ``absent_command_names``;
        (3) neither declared nor declared absent. States (2) and (3) are
        each other's negative space here — this method returns False for
        both and does not distinguish them; a caller that needs to tell
        them apart checks ``absent_command_names`` directly. The refusal
        policy that acts on that distinction (D1's "report unsupported"
        for (2), "must not exist at release" for (3)) is plan §4 Step 4,
        a later change — not implemented by this method.
        """
        return command_name in self.command_names

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
    Path(__file__).resolve().parent.parent.parent.parent
    / "rigs",  # dev: repo root/rigs/
    Path(__file__).resolve().parent.parent / "rigs",  # installed: package/rigs/
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
