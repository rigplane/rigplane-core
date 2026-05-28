"""TOML rig config loader — parse, validate, and build runtime objects."""

from __future__ import annotations

import logging
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rigplane.core.capabilities import KNOWN_CAPABILITIES
from rigplane.commands.command_map import CommandMap

__all__ = ["RigConfig", "RigLoadError", "load_rig", "discover_rigs"]
from rigplane.commands.command_spec import CatCommandSpec, CivCommandSpec, CommandSpec
from rigplane.profiles import (
    BandInfo,
    ControlSpec,
    FilterWidthRule,
    FilterWidthSegment,
    FreqRangeInfo,
    KeyboardBinding,
    KeyboardConfig,
    MeterCalibrationPoint,
    RadioProfile,
    RuleSpec,
)

logger = logging.getLogger(__name__)

VALID_VFO_SCHEMES = {"ab", "main_sub", "ab_shared", "single"}
VALID_PROTOCOL_TYPES = {"civ", "kenwood_cat", "yaesu_cat"}
VALID_CONTROL_STYLES = {
    "toggle",
    "stepped",
    "selector",
    "toggle_and_level",
    "level_is_toggle",
}
VALID_RULE_KINDS = {"mutex", "disables", "requires", "value_limit"}
VALID_KEYBOARD_MODIFIERS = {"SHIFT", "CTRL", "ALT", "META"}
VALID_AUDIO_SAMPLE_RATES_HZ = {8000, 12000, 16000, 24000, 48000}
VALID_BROWSER_RX_TRANSPORTS = {"auto", "pcm", "opus"}
DEFAULT_KEYBOARD_PROFILE_NAME = "_keyboard-default.toml"

_REQUIRED_SECTIONS = ("radio", "capabilities", "modes", "filters", "vfo")
_REQUIRED_RADIO_FIELDS = ("id", "model", "receiver_count", "has_lan", "has_wifi")


class RigLoadError(Exception):
    """Raised when a rig TOML file is invalid or malformed."""


@dataclass(frozen=True, slots=True)
class RigConfig:
    """Parsed rig configuration from a TOML file."""

    id: str
    model: str
    civ_addr: int
    receiver_count: int
    has_lan: bool
    has_wifi: bool
    default_baud: int
    capabilities: tuple[str, ...]
    modes: tuple[str, ...]
    filters: tuple[str, ...]
    vfo_scheme: str
    vfo_main_select: tuple[int, ...] | None
    vfo_sub_select: tuple[int, ...] | None
    vfo_swap_ab: tuple[int, ...] | None
    vfo_equal_ab: tuple[int, ...] | None
    vfo_swap_main_sub: tuple[int, ...] | None
    vfo_equal_main_sub: tuple[int, ...] | None
    freq_ranges: tuple[dict[str, Any], ...]
    commands: dict[str, CommandSpec]
    cmd29_routes: tuple[tuple[int, int | None], ...]
    spectrum: dict[str, int] | None
    att_values: tuple[int, ...] | None
    att_labels: dict[str, str] | None
    pre_values: tuple[int, ...] | None
    pre_labels: dict[str, str] | None
    agc_modes: tuple[int, ...] | None
    agc_labels: dict[str, str] | None
    filter_width_min: int = 50
    filter_width_max: int = 9999
    filter_width_encoding: str = "segmented_bcd_index"
    filter_config: dict[str, FilterWidthRule] | None = None
    data_mode_count: int = 0
    data_mode_labels: dict[str, str] | None = None
    protocol_type: str = "civ"
    protocol_address: int | None = None
    protocol_baud: int | None = None
    controls: dict[str, ControlSpec] | None = None
    meter_calibrations: dict[str, list[MeterCalibrationPoint]] | None = None
    meter_redlines: dict[str, int] | None = None
    rules: tuple[RuleSpec, ...] = ()
    keyboard: KeyboardConfig | None = None
    antenna_tx_count: int = 1
    antenna_has_rx_ant: bool = False
    transceiver_count: int = 1
    # Hamlib rig_model integer (from rigs_list.h). Used by rigctld Yaesu
    # dump_state responses. Default 2028 = RIG_MODEL_FTX1. Icom radios
    # are served by the built-in Icom routing path and don't use this.
    hamlib_model_id: int = 2028
    scope_ref_min_db: float | None = None
    scope_ref_max_db: float | None = None
    scope_ref_step_db: float | None = None
    codec_preference: tuple[str, ...] | None = None
    tx_codec: str | None = None
    default_sample_rate_hz: int | None = None
    supported_sample_rates_hz: tuple[int, ...] | None = None
    sample_rate_by_codec: dict[str, int] | None = None
    browser_rx_transport: str | None = None
    browser_rx_transcode_to_opus: bool | None = None

    def to_profile(self) -> RadioProfile:
        """Build a ``RadioProfile`` from this config."""
        vfo_main = self.vfo_main_select[0] if self.vfo_main_select else None
        vfo_sub = self.vfo_sub_select[0] if self.vfo_sub_select else None
        swap_ab = self.vfo_swap_ab[0] if self.vfo_swap_ab else None
        equal_ab = self.vfo_equal_ab[0] if self.vfo_equal_ab else None
        swap_main_sub = self.vfo_swap_main_sub[0] if self.vfo_swap_main_sub else None
        equal_main_sub = self.vfo_equal_main_sub[0] if self.vfo_equal_main_sub else None

        ranges = tuple(
            FreqRangeInfo(
                start=r["start_hz"],
                end=r["end_hz"],
                label=r["label"],
                bands=tuple(
                    BandInfo(
                        name=b["name"],
                        start=b["start_hz"],
                        end=b["end_hz"],
                        default=b["default_hz"],
                        bsr_code=b.get("bsr_code"),
                    )
                    for b in r.get("bands", ())
                ),
            )
            for r in self.freq_ranges
        )

        return RadioProfile(
            id=self.id,
            model=self.model,
            civ_addr=self.civ_addr,
            receiver_count=self.receiver_count,
            capabilities=frozenset(self.capabilities),
            cmd29_routes=frozenset(self.cmd29_routes),
            vfo_main_code=vfo_main,
            vfo_sub_code=vfo_sub,
            swap_ab_code=swap_ab,
            equal_ab_code=equal_ab,
            swap_main_sub_code=swap_main_sub,
            equal_main_sub_code=equal_main_sub,
            vfo_scheme=self.vfo_scheme,
            has_lan=self.has_lan,
            freq_ranges=ranges,
            modes=tuple(self.modes),
            filters=tuple(self.filters),
            filter_width_min=self.filter_width_min,
            filter_width_max=self.filter_width_max,
            filter_width_encoding=self.filter_width_encoding,
            filter_config=self.filter_config,
            att_values=self.att_values,
            att_labels=self.att_labels,
            pre_values=self.pre_values,
            pre_labels=self.pre_labels,
            agc_modes=self.agc_modes,
            agc_labels=self.agc_labels,
            data_mode_count=self.data_mode_count,
            data_mode_labels=self.data_mode_labels,
            protocol_type=self.protocol_type,
            hamlib_model_id=self.hamlib_model_id,
            controls=self.controls,
            meter_calibrations=self.meter_calibrations,
            meter_redlines=self.meter_redlines,
            rules=self.rules,
            keyboard=self.keyboard,
            antenna_tx_count=self.antenna_tx_count,
            transceiver_count=self.transceiver_count,
            scope_ref_min_db=self.scope_ref_min_db,
            scope_ref_max_db=self.scope_ref_max_db,
            scope_ref_step_db=self.scope_ref_step_db,
            codec_preference=self.codec_preference,
            tx_codec=self.tx_codec,
            default_sample_rate_hz=self.default_sample_rate_hz,
            supported_sample_rates_hz=self.supported_sample_rates_hz,
            sample_rate_by_codec=self.sample_rate_by_codec,
            browser_rx_transport=self.browser_rx_transport,
            browser_rx_transcode_to_opus=self.browser_rx_transcode_to_opus,
        )

    def to_command_map(self) -> CommandMap:
        """Build a ``CommandMap`` from this config's CI-V commands.

        Only CivCommandSpec entries are included; CatCommandSpec entries are ignored.
        """
        civ_commands: dict[str, tuple[int, ...]] = {}
        for name, spec in self.commands.items():
            if isinstance(spec, CivCommandSpec):
                civ_commands[name] = spec.bytes
        return CommandMap(civ_commands)


def _parse_keyboard_binding(
    filename: str,
    binding_raw: dict[str, Any],
    *,
    index: int,
) -> KeyboardBinding:
    binding_id = str(binding_raw.get("id", f"binding-{index}"))
    action = str(binding_raw.get("action", "")).strip()
    if not action:
        raise RigLoadError(
            f"{filename}: [[ui.keyboard.bindings]].action must not be empty"
        )
    if "sequence" in binding_raw:
        sequence_raw = binding_raw["sequence"]
        if not isinstance(sequence_raw, list) or not sequence_raw:
            raise RigLoadError(
                f"{filename}: [[ui.keyboard.bindings]].sequence must be a non-empty list"
            )
        sequence = tuple(str(step) for step in sequence_raw)
    elif "key" in binding_raw:
        sequence = (str(binding_raw["key"]),)
    else:
        raise RigLoadError(
            f"{filename}: [[ui.keyboard.bindings]] must define key or sequence"
        )
    modifiers_raw = binding_raw.get("modifiers", [])
    if not isinstance(modifiers_raw, list):
        raise RigLoadError(
            f"{filename}: [[ui.keyboard.bindings]].modifiers must be a list"
        )
    modifiers = tuple(str(modifier).upper() for modifier in modifiers_raw)
    invalid_modifiers = [m for m in modifiers if m not in VALID_KEYBOARD_MODIFIERS]
    if invalid_modifiers:
        raise RigLoadError(
            f"{filename}: invalid keyboard modifiers {invalid_modifiers!r}; "
            f"expected subset of {sorted(VALID_KEYBOARD_MODIFIERS)}"
        )
    params_raw = binding_raw.get("params")
    params = dict(params_raw) if isinstance(params_raw, dict) else None
    return KeyboardBinding(
        id=binding_id,
        action=action,
        sequence=sequence,
        section=str(binding_raw.get("section", "General")),
        label=(
            str(binding_raw["label"])
            if "label" in binding_raw and binding_raw["label"] is not None
            else None
        ),
        description=(
            str(binding_raw["description"])
            if "description" in binding_raw and binding_raw["description"] is not None
            else None
        ),
        modifiers=modifiers,
        repeatable=bool(binding_raw.get("repeatable", False)),
        params=params,
    )


def _parse_keyboard_config(
    filename: str,
    keyboard_section: dict[str, Any],
) -> KeyboardConfig:
    leader_key = str(keyboard_section.get("leader_key", "g"))
    leader_timeout_ms = int(keyboard_section.get("leader_timeout_ms", 1000))
    alt_hints = bool(keyboard_section.get("alt_hints", True))
    help_title = str(keyboard_section.get("help_title", "Keyboard Shortcuts"))
    bindings_raw = keyboard_section.get("bindings", [])
    bindings: list[KeyboardBinding] = []
    for index, binding_raw in enumerate(bindings_raw, start=1):
        if not isinstance(binding_raw, dict):
            raise RigLoadError(
                f"{filename}: [[ui.keyboard.bindings]] entry #{index} must be a table"
            )
        bindings.append(_parse_keyboard_binding(filename, binding_raw, index=index))
    return KeyboardConfig(
        leader_key=leader_key,
        leader_timeout_ms=leader_timeout_ms,
        alt_hints=alt_hints,
        help_title=help_title,
        bindings=tuple(bindings),
    )


def _load_keyboard_file(
    path: Path, keyboard_path: Path, *, optional: bool = False
) -> KeyboardConfig | None:
    include_name = keyboard_path.name
    if not keyboard_path.exists():
        if optional:
            return None
        raise RigLoadError(
            f"{path.name}: keyboard profile file not found: {keyboard_path.name}"
        )
    try:
        data = tomllib.loads(keyboard_path.read_text())
    except Exception as exc:
        raise RigLoadError(
            f"{path.name}: failed to parse keyboard profile {include_name}: {exc}"
        ) from exc
    keyboard_section = data.get("keyboard", data)
    if not isinstance(keyboard_section, dict):
        raise RigLoadError(
            f"{path.name}: keyboard profile {include_name} must contain a [keyboard] table or root mapping"
        )
    return _parse_keyboard_config(include_name, keyboard_section)


def _load_default_keyboard_config(path: Path) -> KeyboardConfig | None:
    return _load_keyboard_file(
        path, path.parent / DEFAULT_KEYBOARD_PROFILE_NAME, optional=True
    )


def _parse_command_value(
    filename: str,
    command_name: str,
    value: Any,
) -> CommandSpec:
    """Parse a single command value from TOML.

    Supports two formats:
    1. CI-V wire bytes (list): [0x03] or [0x14, 0x01]
    2. CAT command spec (dict): { cat = { read = "FA;", parse = "FA{freq:09d};" } }

    Args:
        filename: Source TOML filename (for error messages).
        command_name: Command name (for error messages).
        value: Raw TOML value to parse.

    Returns:
        Parsed CommandSpec (either CivCommandSpec or CatCommandSpec).

    Raises:
        RigLoadError: If the value format is invalid.
    """
    # Format 1: CI-V wire bytes (list of integers)
    if isinstance(value, list):
        if not value:
            raise RigLoadError(
                f"{filename}: [commands].{command_name} = [] (empty list not allowed)"
            )
        if not all(isinstance(byte, int) for byte in value):
            raise RigLoadError(
                f"{filename}: [commands].{command_name} must be all integers, "
                f"got {value!r}"
            )
        if not all(0x00 <= byte <= 0xFF for byte in value):
            raise RigLoadError(
                f"{filename}: [commands].{command_name} bytes must be 0x00–0xFF, "
                f"got {value!r}"
            )
        return CivCommandSpec(bytes=tuple(value))

    # Format 2: CAT command spec (dict with 'cat' key)
    if isinstance(value, dict):
        if "cat" not in value:
            raise RigLoadError(
                f"{filename}: [commands].{command_name} dict must have 'cat' key, "
                f"got keys: {sorted(value.keys())}"
            )
        cat_spec = value["cat"]
        if not isinstance(cat_spec, dict):
            raise RigLoadError(
                f"{filename}: [commands].{command_name}.cat must be a dict, "
                f"got {type(cat_spec).__name__}"
            )

        read_cmd = cat_spec.get("read")
        write_cmd = cat_spec.get("write")
        parse_template = cat_spec.get("parse")

        # Validate types
        if read_cmd is not None and not isinstance(read_cmd, str):
            raise RigLoadError(
                f"{filename}: [commands].{command_name}.cat.read must be a string"
            )
        if write_cmd is not None and not isinstance(write_cmd, str):
            raise RigLoadError(
                f"{filename}: [commands].{command_name}.cat.write must be a string"
            )
        if parse_template is not None and not isinstance(parse_template, str):
            raise RigLoadError(
                f"{filename}: [commands].{command_name}.cat.parse must be a string"
            )

        # At least one of read/write must be present
        if read_cmd is None and write_cmd is None:
            raise RigLoadError(
                f"{filename}: [commands].{command_name}.cat must have "
                f"at least one of 'read' or 'write'"
            )

        return CatCommandSpec(read=read_cmd, write=write_cmd, parse=parse_template)

    # Unknown format
    raise RigLoadError(
        f"{filename}: [commands].{command_name} must be a list (CI-V bytes) "
        f"or dict (CAT spec), got {type(value).__name__}"
    )


def _merge_keyboard_config(
    base: KeyboardConfig | None,
    override_section: dict[str, Any],
    *,
    filename: str,
) -> KeyboardConfig | None:
    if base is None and not override_section:
        return None

    leader_key = str(
        override_section.get("leader_key", base.leader_key if base else "g")
    )
    leader_timeout_ms = int(
        override_section.get(
            "leader_timeout_ms",
            base.leader_timeout_ms if base else 1000,
        )
    )
    alt_hints = bool(
        override_section.get("alt_hints", base.alt_hints if base else True)
    )
    help_title = str(
        override_section.get(
            "help_title", base.help_title if base else "Keyboard Shortcuts"
        )
    )

    merged_bindings: dict[str, KeyboardBinding] = {
        binding.id: binding for binding in (base.bindings if base else ())
    }
    bindings_raw = override_section.get("bindings", [])
    for index, binding_raw in enumerate(bindings_raw, start=1):
        if not isinstance(binding_raw, dict):
            raise RigLoadError(
                f"{filename}: [[ui.keyboard.bindings]] entry #{index} must be a table"
            )
        binding = _parse_keyboard_binding(filename, binding_raw, index=index)
        merged_bindings[binding.id] = binding

    return KeyboardConfig(
        leader_key=leader_key,
        leader_timeout_ms=leader_timeout_ms,
        alt_hints=alt_hints,
        help_title=help_title,
        bindings=tuple(merged_bindings.values()),
    )


def _valid_audio_codec_names() -> set[str]:
    from rigplane.types import AudioCodec

    return {codec.name for codec in AudioCodec}


def _validate_audio_codec_name(
    filename: str,
    field_name: str,
    value: Any,
    valid_names: set[str],
) -> str:
    if not isinstance(value, str):
        raise RigLoadError(f"{filename}: [audio].{field_name} must be a string")
    if value not in valid_names:
        raise RigLoadError(
            f"{filename}: [audio].{field_name} has unknown codec {value!r}. "
            f"Valid names: {sorted(valid_names)}"
        )
    return value


def _validate_audio_sample_rate(filename: str, field_name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RigLoadError(f"{filename}: [audio].{field_name} must be an integer")
    if value <= 0 or value not in VALID_AUDIO_SAMPLE_RATES_HZ:
        raise RigLoadError(
            f"{filename}: [audio].{field_name} must be one of "
            f"{sorted(VALID_AUDIO_SAMPLE_RATES_HZ)}, got {value!r}"
        )
    return value


def load_rig(path: Path) -> RigConfig:
    """Load and validate a rig TOML file.

    Args:
        path: Path to the ``.toml`` file.

    Returns:
        Parsed and validated ``RigConfig``.

    Raises:
        RigLoadError: If the file is missing, unparseable, or invalid.
    """
    filename = path.name

    if not path.exists():
        raise RigLoadError(f"{filename}: file not found: {path}")

    try:
        raw = path.read_bytes()
        data = tomllib.loads(raw.decode())
    except Exception as exc:
        raise RigLoadError(f"{filename}: failed to parse TOML: {exc}") from exc

    # Validate required sections
    for section in _REQUIRED_SECTIONS:
        if section not in data:
            raise RigLoadError(f"{filename}: missing required section [{section}]")

    # Validate [radio]
    radio = data["radio"]
    for field_name in _REQUIRED_RADIO_FIELDS:
        if field_name not in radio:
            raise RigLoadError(
                f"{filename}: missing required field [radio].{field_name}"
            )

    # civ_addr is optional (default 0 for non-civ radios); validate range if present
    if "civ_addr" in radio:
        civ_addr = radio["civ_addr"]
        if not (0x00 <= civ_addr <= 0xFF):
            raise RigLoadError(
                f"{filename}: [radio].civ_addr = {civ_addr} out of range 0x00–0xFF"
            )
    else:
        civ_addr = 0

    # Validate [capabilities]
    features = data["capabilities"].get("features", [])
    if not features:
        raise RigLoadError(f"{filename}: [capabilities].features must not be empty")
    for cap in features:
        if cap not in KNOWN_CAPABILITIES:
            raise RigLoadError(
                f"{filename}: unknown capability {cap!r}. "
                f"Known: {sorted(KNOWN_CAPABILITIES)}"
            )

    # Validate [vfo]
    vfo = data["vfo"]
    scheme = vfo.get("scheme", "")
    if scheme not in VALID_VFO_SCHEMES:
        raise RigLoadError(
            f"{filename}: [vfo].scheme must be one of {VALID_VFO_SCHEMES}, "
            f"got {scheme!r}"
        )

    # Validate [modes]
    modes = data["modes"].get("list", [])
    if not modes:
        raise RigLoadError(f"{filename}: [modes].list must not be empty")

    # Validate [filters]
    filter_section = data["filters"]
    filters = filter_section.get("list", [])
    if not filters:
        raise RigLoadError(f"{filename}: [filters].list must not be empty")
    filter_width_min = int(filter_section.get("width_min_hz", 50))
    filter_width_max = int(filter_section.get("width_max_hz", 9999))
    filter_width_encoding = str(filter_section.get("encoding", "segmented_bcd_index"))
    filter_config_raw = filter_section.get("width", {})
    filter_config: dict[str, FilterWidthRule] | None = None
    if isinstance(filter_config_raw, dict) and filter_config_raw:
        filter_config = {}
        for mode_key, raw_rule in filter_config_raw.items():
            if not isinstance(raw_rule, dict):
                raise RigLoadError(
                    f"{filename}: [filters.width].{mode_key} must be a table"
                )
            raw_segments = raw_rule.get("segments", [])
            segments = tuple(
                FilterWidthSegment(
                    hz_min=int(segment["hz_min"]),
                    hz_max=int(segment["hz_max"]),
                    step_hz=int(segment["step_hz"]),
                    index_min=int(segment["index_min"]),
                )
                for segment in raw_segments
            )
            defaults_raw = raw_rule.get("defaults", [])
            table_raw = raw_rule.get("table", [])
            filter_config[str(mode_key).upper()] = FilterWidthRule(
                defaults=tuple(int(value) for value in defaults_raw),
                fixed=bool(raw_rule.get("fixed", False)),
                step_hz=(int(raw_rule["step_hz"]) if "step_hz" in raw_rule else None),
                min_hz=(int(raw_rule["min_hz"]) if "min_hz" in raw_rule else None),
                max_hz=(int(raw_rule["max_hz"]) if "max_hz" in raw_rule else None),
                segments=segments,
                table=tuple(int(v) for v in table_raw),
            )

    # Parse [protocol] (optional)
    proto_section = data.get("protocol", {})
    protocol_type = proto_section.get("type", "civ")
    if protocol_type not in VALID_PROTOCOL_TYPES:
        raise RigLoadError(
            f"{filename}: [protocol].type must be one of {VALID_PROTOCOL_TYPES}, "
            f"got {protocol_type!r}"
        )
    protocol_address = proto_section.get("address")
    protocol_baud = proto_section.get("baud")

    # Parse commands (optional for non-civ protocols)
    commands: dict[str, CommandSpec] = {}
    if "commands" in data:
        commands_raw = dict(data["commands"])
        overrides = commands_raw.pop("overrides", {})

        # Parse main commands
        for key, value in commands_raw.items():
            commands[key] = _parse_command_value(filename, key, value)

        # Apply overrides
        for key, value in overrides.items():
            commands[key] = _parse_command_value(filename, key, value)

    # Parse freq_ranges
    freq_ranges_data = data.get("freq_ranges", {}).get("ranges", [])

    # Parse VFO bytes — explicit split (issue #710)
    vfo_main = tuple(vfo["main_select"]) if "main_select" in vfo else None
    vfo_sub = tuple(vfo["sub_select"]) if "sub_select" in vfo else None
    vfo_swap_ab = tuple(vfo["swap_ab"]) if "swap_ab" in vfo else None
    vfo_equal_ab = tuple(vfo["equal_ab"]) if "equal_ab" in vfo else None
    vfo_swap_main_sub = tuple(vfo["swap_main_sub"]) if "swap_main_sub" in vfo else None
    vfo_equal_main_sub = (
        tuple(vfo["equal_main_sub"]) if "equal_main_sub" in vfo else None
    )

    # Legacy keys — map to new fields based on scheme; warn once per file.
    has_legacy = "swap" in vfo or "equal" in vfo
    if has_legacy:
        legacy_swap = tuple(vfo["swap"]) if "swap" in vfo else None
        legacy_equal = tuple(vfo["equal"]) if "equal" in vfo else None
        if scheme == "main_sub":
            if legacy_swap is not None and vfo_swap_main_sub is None:
                vfo_swap_main_sub = legacy_swap
            if legacy_equal is not None and vfo_equal_main_sub is None:
                vfo_equal_main_sub = legacy_equal
        else:
            if legacy_swap is not None and vfo_swap_ab is None:
                vfo_swap_ab = legacy_swap
            if legacy_equal is not None and vfo_equal_ab is None:
                vfo_equal_ab = legacy_equal
        msg = (
            f"{filename}: [vfo].swap/[vfo].equal are deprecated; "
            "use swap_ab/equal_ab or swap_main_sub/equal_main_sub "
            "(issue #710)."
        )
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
        logger.warning(msg)

    # Parse cmd29 routes
    cmd29_raw = data.get("cmd29", {}).get("routes", [])
    cmd29_routes: list[tuple[int, int | None]] = []
    for entry in cmd29_raw:
        if len(entry) == 1:
            cmd29_routes.append((entry[0], None))
        elif len(entry) == 2:
            cmd29_routes.append((entry[0], entry[1]))

    # Parse spectrum
    spectrum = data.get("spectrum")

    # Parse [scope] (optional)
    scope_section = data.get("scope", {})
    scope_ref_min_db: float | None = None
    scope_ref_max_db: float | None = None
    scope_ref_step_db: float | None = None
    if scope_section:
        scope_ref_min_db = (
            float(scope_section["ref_min_db"])
            if "ref_min_db" in scope_section
            else None
        )
        scope_ref_max_db = (
            float(scope_section["ref_max_db"])
            if "ref_max_db" in scope_section
            else None
        )
        scope_ref_step_db = (
            float(scope_section["ref_step_db"])
            if "ref_step_db" in scope_section
            else None
        )

    # Parse attenuator/preamp/agc (optional sections)
    att_section = data.get("attenuator", {})
    att_values = tuple(att_section["values"]) if "values" in att_section else None
    att_labels = dict(att_section["labels"]) if "labels" in att_section else None

    pre_section = data.get("preamp", {})
    pre_values = tuple(pre_section["values"]) if "values" in pre_section else None
    pre_labels = dict(pre_section["labels"]) if "labels" in pre_section else None

    agc_section = data.get("agc", {})
    agc_modes = tuple(agc_section["modes"]) if "modes" in agc_section else None
    agc_labels = dict(agc_section["labels"]) if "labels" in agc_section else None

    # Parse [data_mode] (optional)
    # If data_mode is in features but no [data_mode] section, default to 1 mode (OFF/DATA)
    data_mode_section = data.get("data_mode", {})
    has_data_mode_feature = "data_mode" in features
    if data_mode_section:
        data_mode_count = int(data_mode_section.get("count", 0))
        data_mode_labels = (
            dict(data_mode_section["labels"]) if "labels" in data_mode_section else None
        )
    elif has_data_mode_feature:
        data_mode_count = 1
        data_mode_labels = {"0": "OFF", "1": "DATA"}
    else:
        data_mode_count = 0
        data_mode_labels = None

    # Parse [controls] (optional)
    controls_raw = data.get("controls")
    controls: dict[str, ControlSpec] | None = None
    if controls_raw is not None:
        controls = {}
        for ctrl_name, ctrl_data in controls_raw.items():
            if isinstance(ctrl_data, dict):
                style = ctrl_data.get("style")
                if style is not None and style not in VALID_CONTROL_STYLES:
                    raise RigLoadError(
                        f"{filename}: [controls.{ctrl_name}].style must be one of "
                        f"{VALID_CONTROL_STYLES}, got {style!r}"
                    )
                controls[ctrl_name] = dict(ctrl_data)  # type: ignore[assignment]

    # Parse [meters] (optional)
    meters_raw = data.get("meters")
    meter_calibrations: dict[str, list[MeterCalibrationPoint]] | None = None
    meter_redlines: dict[str, int] | None = None
    if meters_raw is not None:
        meter_calibrations = {}
        meter_redlines = {}
        for meter_name, meter_data in meters_raw.items():
            if isinstance(meter_data, dict):
                if "calibration" in meter_data:
                    meter_calibrations[meter_name] = list(meter_data["calibration"])
                if "redline_raw" in meter_data:
                    meter_redlines[meter_name] = meter_data["redline_raw"]
        if not meter_calibrations:
            meter_calibrations = None
        if not meter_redlines:
            meter_redlines = None

    # Parse [[rules]] (optional)
    rules_raw = data.get("rules", [])
    rules: list[RuleSpec] = []
    for rule in rules_raw:
        kind = rule.get("kind")
        if kind not in VALID_RULE_KINDS:
            raise RigLoadError(
                f"{filename}: rule kind must be one of {VALID_RULE_KINDS}, got {kind!r}"
            )
        rules.append(dict(rule))  # type: ignore[arg-type]

    # Parse [antenna] (optional)
    antenna_section = data.get("antenna", {})
    antenna_tx_count = int(antenna_section.get("tx_count", 1))
    antenna_has_rx_ant = bool(antenna_section.get("has_rx_ant", False))

    # Parse keyboard config: shared default profile + optional rig-local overrides.
    ui_section = data.get("ui", {})
    keyboard_section = (
        ui_section.get("keyboard", {}) if isinstance(ui_section, dict) else {}
    )
    base_keyboard = _load_default_keyboard_config(path)
    override_section = keyboard_section if isinstance(keyboard_section, dict) else {}
    keyboard = _merge_keyboard_config(
        base_keyboard,
        override_section,
        filename=filename,
    )

    # Parse optional [audio] codec and sample-rate policy (#797, #1470).
    codec_preference: tuple[str, ...] | None = None
    tx_codec: str | None = None
    default_sample_rate_hz: int | None = None
    supported_sample_rates_hz: tuple[int, ...] | None = None
    sample_rate_by_codec: dict[str, int] | None = None
    browser_rx_transport: str | None = None
    browser_rx_transcode_to_opus: bool | None = None
    audio_section = data.get("audio")
    if audio_section is not None:
        if not isinstance(audio_section, dict):
            raise RigLoadError(f"{filename}: [audio] must be a table")
        valid_codec_names = _valid_audio_codec_names()
        codec_raw = audio_section.get("codec_preference")
        if codec_raw is not None:
            if not isinstance(codec_raw, list) or not all(
                isinstance(c, str) for c in codec_raw
            ):
                raise RigLoadError(
                    f"{filename}: [audio].codec_preference must be a list of strings"
                )
            if not codec_raw:
                raise RigLoadError(
                    f"{filename}: [audio].codec_preference must not be empty"
                )
            unknown = [c for c in codec_raw if c not in valid_codec_names]
            if unknown:
                raise RigLoadError(
                    f"{filename}: [audio].codec_preference has unknown codec(s): "
                    f"{unknown}. Valid names: {sorted(valid_codec_names)}"
                )
            codec_preference = tuple(codec_raw)
        if "tx_codec" in audio_section:
            tx_codec = _validate_audio_codec_name(
                filename, "tx_codec", audio_section["tx_codec"], valid_codec_names
            )
        if "default_sample_rate_hz" in audio_section:
            default_sample_rate_hz = _validate_audio_sample_rate(
                filename,
                "default_sample_rate_hz",
                audio_section["default_sample_rate_hz"],
            )
        if "supported_sample_rates_hz" in audio_section:
            supported_raw = audio_section["supported_sample_rates_hz"]
            if not isinstance(supported_raw, list) or not supported_raw:
                raise RigLoadError(
                    f"{filename}: [audio].supported_sample_rates_hz must be a non-empty list"
                )
            supported_sample_rates_hz = tuple(
                _validate_audio_sample_rate(filename, "supported_sample_rates_hz", rate)
                for rate in supported_raw
            )
        if "sample_rate_by_codec" in audio_section:
            by_codec_raw = audio_section["sample_rate_by_codec"]
            if not isinstance(by_codec_raw, dict) or not by_codec_raw:
                raise RigLoadError(
                    f"{filename}: [audio].sample_rate_by_codec must be a non-empty table"
                )
            sample_rate_by_codec = {}
            for codec_name, sample_rate in by_codec_raw.items():
                codec_key = _validate_audio_codec_name(
                    filename,
                    "sample_rate_by_codec",
                    codec_name,
                    valid_codec_names,
                )
                sample_rate_by_codec[codec_key] = _validate_audio_sample_rate(
                    filename,
                    f"sample_rate_by_codec.{codec_key}",
                    sample_rate,
                )
        if "browser_rx_transport" in audio_section:
            browser_rx_transport_raw = audio_section["browser_rx_transport"]
            if not isinstance(browser_rx_transport_raw, str):
                raise RigLoadError(
                    f"{filename}: [audio].browser_rx_transport must be a string"
                )
            if browser_rx_transport_raw not in VALID_BROWSER_RX_TRANSPORTS:
                raise RigLoadError(
                    f"{filename}: [audio].browser_rx_transport must be one of "
                    f"{sorted(VALID_BROWSER_RX_TRANSPORTS)}, got {browser_rx_transport_raw!r}"
                )
            browser_rx_transport = browser_rx_transport_raw
        if "browser_rx_transcode_to_opus" in audio_section:
            transcode_raw = audio_section["browser_rx_transcode_to_opus"]
            if not isinstance(transcode_raw, bool):
                raise RigLoadError(
                    f"{filename}: [audio].browser_rx_transcode_to_opus must be a boolean"
                )
            browser_rx_transcode_to_opus = transcode_raw

    return RigConfig(
        id=radio["id"],
        model=radio["model"],
        civ_addr=civ_addr,
        receiver_count=radio["receiver_count"],
        transceiver_count=int(radio.get("transceiver_count", 1)),
        hamlib_model_id=int(radio.get("hamlib_model_id", 2028)),
        has_lan=radio["has_lan"],
        has_wifi=radio["has_wifi"],
        default_baud=radio.get("default_baud", 19200),
        capabilities=tuple(features),
        modes=tuple(modes),
        filters=tuple(filters),
        filter_width_min=filter_width_min,
        filter_width_max=filter_width_max,
        filter_width_encoding=filter_width_encoding,
        filter_config=filter_config,
        vfo_scheme=scheme,
        vfo_main_select=vfo_main,
        vfo_sub_select=vfo_sub,
        vfo_swap_ab=vfo_swap_ab,
        vfo_equal_ab=vfo_equal_ab,
        vfo_swap_main_sub=vfo_swap_main_sub,
        vfo_equal_main_sub=vfo_equal_main_sub,
        freq_ranges=tuple(freq_ranges_data),
        commands=commands,
        cmd29_routes=tuple(cmd29_routes),
        spectrum=spectrum,
        att_values=att_values,
        att_labels=att_labels,
        pre_values=pre_values,
        pre_labels=pre_labels,
        agc_modes=agc_modes,
        agc_labels=agc_labels,
        data_mode_count=data_mode_count,
        data_mode_labels=data_mode_labels,
        protocol_type=protocol_type,
        protocol_address=protocol_address,
        protocol_baud=protocol_baud,
        controls=controls,
        meter_calibrations=meter_calibrations,
        meter_redlines=meter_redlines,
        rules=tuple(rules),
        keyboard=keyboard,
        antenna_tx_count=antenna_tx_count,
        antenna_has_rx_ant=antenna_has_rx_ant,
        scope_ref_min_db=scope_ref_min_db,
        scope_ref_max_db=scope_ref_max_db,
        scope_ref_step_db=scope_ref_step_db,
        codec_preference=codec_preference,
        tx_codec=tx_codec,
        default_sample_rate_hz=default_sample_rate_hz,
        supported_sample_rates_hz=supported_sample_rates_hz,
        sample_rate_by_codec=sample_rate_by_codec,
        browser_rx_transport=browser_rx_transport,
        browser_rx_transcode_to_opus=browser_rx_transcode_to_opus,
    )


def discover_rigs(directory: Path) -> dict[str, RigConfig]:
    """Discover and load all rig TOML files in a directory.

    Files starting with underscore are ignored (e.g. ``_schema.md``,
    ``_template.toml``).

    Returns:
        Dict mapping model name to ``RigConfig``.
    """
    rigs: dict[str, RigConfig] = {}
    if not directory.is_dir():
        return rigs

    for path in sorted(directory.glob("*.toml")):
        if path.name.startswith("_"):
            continue
        rig = load_rig(path)
        rigs[rig.model] = rig

    return rigs
