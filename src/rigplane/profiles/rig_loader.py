"""TOML rig config loader — parse, validate, and build runtime objects."""

from __future__ import annotations

import logging
import tempfile
import tomllib
import warnings
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, cast

from rigplane.core.capabilities import KNOWN_CAPABILITIES
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    AdaptiveDecayPolicy,
    ExternalCatPauseBehavior,
    FieldAvailability,
    FieldCapability,
    MeterCoalescingPolicy,
    RadioAcquisitionProfile,
    ReconciliationPriority,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.tx_interlock_contract import (
    TX_INTERLOCK_COMMAND_FAMILY_METADATA,
    TxInterlockCommandFamily,
    TxInterlockDisposition,
)
from rigplane.commands.command_map import CommandMap

__all__ = [
    "RigConfig",
    "RigLoadError",
    "load_rig",
    "discover_rigs",
    "discover_available_rigs",
]
from rigplane.commands.command_spec import (
    AbsentCommandSpec,
    CatCommandSpec,
    CivCommandSpec,
    CommandSpec,
)
from rigplane.profiles import (
    BandInfo,
    ControlDomainSpec,
    ControlLookupPoint,
    EncodedControlChoice,
    ControlSpec,
    FilterWidthRule,
    FilterWidthSegment,
    FreqRangeInfo,
    KeyboardBinding,
    KeyboardConfig,
    MeterCalibrationPoint,
    RadioProfile,
    RuleSpec,
    TxPolicy,
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
VALID_CONTROL_MAPPINGS = {"identity", "linear", "centered", "lookup", "encoded"}
VALID_CONTROL_QUANTIZATION = {
    "nearest_ties_down",
    "nearest_ties_up",
    "floor",
    "ceil",
    "reject",
}
VALID_CONTROL_RESTORATION = {"exact", "unavailable"}
_CONTROL_KEYS = {
    "style",
    "range_min",
    "range_max",
    "raw_min",
    "raw_max",
    "raw_step",
    "raw_origin",
    "raw_center",
    "display_min",
    "display_max",
    "display_step",
    "display_origin",
    "display_center",
    "display_unit",
    "mapping",
    "quantization",
    "restoration",
    "lookup",
    "choices",
}
_EXPLICIT_CONTROL_DOMAIN_KEYS = {
    "raw_step",
    "raw_origin",
    "display_step",
    "display_origin",
    "display_center",
    "mapping",
    "quantization",
    "restoration",
    "lookup",
    "choices",
}
VALID_RULE_KINDS = {"mutex", "disables", "requires", "value_limit"}
VALID_KEYBOARD_MODIFIERS = {"SHIFT", "CTRL", "ALT", "META"}
VALID_AUDIO_SAMPLE_RATES_HZ = {8000, 12000, 16000, 24000, 48000}
VALID_BROWSER_RX_TRANSPORTS = {"auto", "pcm", "opus"}
VALID_RX_AUDIO_CHANNELS = {"mix", "left", "right"}
VALID_VFO_READBACK = {"absolute", "selected_unselected", "none"}
# MOR-1447 leg 2. Icom hardware wires RF gain and squelch as one physical
# knob (hard left = RF min/SQL min, center = RF max/SQL min, hard right =
# SQL max/RF max); "separate" is the default two-independent-controls model
# every other rig uses.
VALID_RF_SQL_CONTROL_MODELS = {"separate", "combined"}
DEFAULT_KEYBOARD_PROFILE_NAME = "_keyboard-default.toml"

_REQUIRED_SECTIONS = ("radio", "capabilities", "modes", "filters", "vfo")
_REQUIRED_RADIO_FIELDS = ("id", "model", "receiver_count", "has_lan", "has_wifi")


class RigLoadError(Exception):
    """Raised when a rig TOML file is invalid or malformed."""


_LookupPoint = tuple[int, Decimal]
_EncodedChoice = tuple[int, Decimal | str]
_ScalarControlDomain = dict[
    str, str | int | Decimal | tuple[_LookupPoint, ...] | tuple[_EncodedChoice, ...]
]


def _public_decimal(value: Decimal) -> str:
    """Render an exact Decimal as the frontend's canonical fixed-point string."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"0", "-0"} else rendered


def _control_number(value: object, path: str, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        expected = "an integer" if integer else "a finite number"
        raise RigLoadError(f"{path} must be {expected}")
    if integer and not isinstance(value, int):
        raise RigLoadError(f"{path} must be an integer")
    if isinstance(value, float) and not Decimal(str(value)).is_finite():
        raise RigLoadError(f"{path} must be a finite number")
    return value


def _control_decimal(value: object, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RigLoadError(f"{path} must be a finite number")
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise RigLoadError(f"{path} must be a finite number")
    return decimal


def _on_control_lattice(
    value: int | Decimal, origin: int | Decimal, step: int | Decimal
) -> bool:
    (value_num, value_den), (origin_num, origin_den), (step_num, step_den) = (
        Decimal(item).as_integer_ratio() for item in (value, origin, step)
    )
    numerator = (value_num * origin_den - origin_num * value_den) * step_den
    return numerator % (value_den * origin_den * step_num) == 0


def _parse_control_lookup(
    raw_lookup: object,
    prefix: str,
    raw_range: tuple[int, int, int, int],
    display_range: tuple[Decimal, Decimal, Decimal, Decimal],
    restoration: str,
) -> tuple[_LookupPoint, ...]:
    if not isinstance(raw_lookup, list) or not raw_lookup:
        raise RigLoadError(f"{prefix}.lookup must be a non-empty array")
    raw_min, raw_max, raw_step, raw_origin = raw_range
    display_min, display_max, display_step, display_origin = display_range
    points: list[_LookupPoint] = []
    for index, point in enumerate(raw_lookup):
        point_prefix = f"{prefix}.lookup[{index}]"
        if not isinstance(point, dict) or set(point) != {"raw", "display"}:
            raise RigLoadError(
                f"{point_prefix} must be a table containing exactly raw and display"
            )
        raw_value = int(
            _control_number(point["raw"], f"{point_prefix}.raw", integer=True)
        )
        display_value = _control_decimal(point["display"], f"{point_prefix}.display")
        if not raw_min <= raw_value <= raw_max:
            raise RigLoadError(f"{point_prefix}.raw must be inside its declared range")
        if not _on_control_lattice(raw_value, raw_origin, raw_step):
            raise RigLoadError(f"{point_prefix}.raw must lie on its declared lattice")
        if not display_min <= display_value <= display_max:
            raise RigLoadError(
                f"{point_prefix}.display must be inside its declared range"
            )
        if not _on_control_lattice(display_value, display_origin, display_step):
            raise RigLoadError(
                f"{point_prefix}.display must lie on its declared lattice"
            )
        points.append((raw_value, display_value))

    axes: tuple[tuple[str, list[int | Decimal]], ...] = (
        ("raw", [point[0] for point in points]),
        ("display", [point[1] for point in points]),
    )
    for name, values in axes:
        if len(set(values)) != len(values):
            raise RigLoadError(f"{prefix}.lookup {name} values must be unique")
        increasing = all(first < second for first, second in zip(values, values[1:]))
        decreasing = all(first > second for first, second in zip(values, values[1:]))
        if len(values) > 1 and not (increasing or decreasing):
            raise RigLoadError(
                f"{prefix}.lookup {name} values must be strictly monotonic"
            )

    expected_raw_points = (raw_max - raw_min) // raw_step + 1
    if restoration == "exact" and len(points) != expected_raw_points:
        raise RigLoadError(
            f"{prefix}.lookup exact restoration requires complete raw lattice coverage"
        )
    return tuple(points)


def _parse_encoded_control_choices(
    raw_choices: object, prefix: str
) -> tuple[_EncodedChoice, ...]:
    if not isinstance(raw_choices, list) or not raw_choices:
        raise RigLoadError(f"{prefix}.choices must be a non-empty array")

    choices: list[_EncodedChoice] = []
    numeric_values: list[Decimal] = []
    labeled_choices: list[tuple[int, str, str]] = []
    for index, choice in enumerate(raw_choices):
        choice_prefix = f"{prefix}.choices[{index}]"
        if not isinstance(choice, dict) or set(choice) not in (
            {"raw", "label"},
            {"raw", "display"},
        ):
            raise RigLoadError(
                f"{choice_prefix} must contain exactly raw and one of label or display"
            )
        raw_value = int(
            _control_number(choice["raw"], f"{choice_prefix}.raw", integer=True)
        )
        if "label" in choice:
            label = choice["label"]
            if not isinstance(label, str) or not label.strip():
                raise RigLoadError(f"{choice_prefix}.label must be a non-empty string")
            choices.append((raw_value, label))
            labeled_choices.append((raw_value, label, choice_prefix))
        else:
            display = _control_decimal(choice["display"], f"{choice_prefix}.display")
            choices.append((raw_value, display))
            numeric_values.append(display)

    raw_values = [choice[0] for choice in choices]
    if len(set(raw_values)) != len(raw_values):
        raise RigLoadError(f"{prefix}.choices raw values must be unique")
    if len(set(numeric_values)) != len(numeric_values):
        raise RigLoadError(f"{prefix}.choices display values must be unique")
    if len(labeled_choices) != 1:
        raise RigLoadError(
            f"{prefix}.choices must contain exactly one default label choice"
        )
    default_raw, default_label, default_prefix = labeled_choices[0]
    if default_raw != 0:
        raise RigLoadError(f"{default_prefix}.label default must use raw code 0")
    if default_label != "Default":
        raise RigLoadError(f'{default_prefix}.label must be exactly "Default"')
    return tuple(choices)


def _parse_control_spec(
    filename: str, control_name: str, raw: object
) -> tuple[ControlSpec | None, _ScalarControlDomain | None]:
    prefix = f"{filename}: [controls.{control_name}]"
    if not isinstance(raw, dict):
        raise RigLoadError(f"{prefix} must be a table")

    unknown = sorted(set(raw) - _CONTROL_KEYS)
    if unknown:
        raise RigLoadError(f"{prefix} unknown key(s): {unknown!r}")

    style = raw.get("style")
    if style is not None and (
        not isinstance(style, str) or style not in VALID_CONTROL_STYLES
    ):
        raise RigLoadError(
            f"{prefix}.style must be one of {VALID_CONTROL_STYLES}, got {style!r}"
        )

    for first, last, integer in (
        ("range_min", "range_max", True),
        ("raw_min", "raw_max", True),
        ("display_min", "display_max", False),
    ):
        present = [key in raw for key in (first, last)]
        if any(present) and not all(present):
            raise RigLoadError(f"{prefix}.{first} and {last} must be declared together")
        if all(present):
            low = _control_number(raw[first], f"{prefix}.{first}", integer=integer)
            high = _control_number(raw[last], f"{prefix}.{last}", integer=integer)
            if low >= high:
                raise RigLoadError(f"{prefix}.{first} must be less than {last}")
    if "raw_center" in raw:
        _control_number(raw["raw_center"], f"{prefix}.raw_center", integer=True)
    if "display_center" in raw:
        _control_number(raw["display_center"], f"{prefix}.display_center")
    if "display_unit" in raw and not isinstance(raw["display_unit"], str):
        raise RigLoadError(f"{prefix}.display_unit must be a string")

    explicit = bool(set(raw) & _EXPLICIT_CONTROL_DOMAIN_KEYS)
    if not explicit:
        return cast(ControlSpec, dict(raw)), None
    if "mapping" not in raw:
        raise RigLoadError(f"{prefix}.mapping is required for an explicit domain")

    mapping = raw["mapping"]
    if not isinstance(mapping, str) or mapping not in VALID_CONTROL_MAPPINGS:
        raise RigLoadError(
            f"{prefix}.mapping must be one of {sorted(VALID_CONTROL_MAPPINGS)!r}"
        )
    if mapping != "encoded" and "choices" in raw:
        raise RigLoadError(f"{prefix}.choices requires encoded mapping")
    if mapping == "encoded":
        if "choices" not in raw:
            raise RigLoadError(f"{prefix}.choices is required for encoded mapping")
        encoded_only = {"mapping", "choices", "style"}
        unsupported = sorted(set(raw) - encoded_only)
        if unsupported:
            raise RigLoadError(
                f"{prefix}.encoded mapping does not accept key(s): {unsupported!r}"
            )
        choices = _parse_encoded_control_choices(raw["choices"], prefix)
        encoded_domain: _ScalarControlDomain = {"mapping": mapping, "choices": choices}
        encoded_public_spec: ControlSpec | None = (
            {"style": style} if style is not None else None
        )
        return encoded_public_spec, encoded_domain
    if mapping == "lookup" and "lookup" not in raw:
        raise RigLoadError(f"{prefix}.lookup is required for lookup mapping")
    if mapping != "lookup" and "lookup" in raw:
        raise RigLoadError(f"{prefix}.lookup requires lookup mapping")
    required = {
        "raw_min",
        "raw_max",
        "raw_step",
        "raw_origin",
        "display_min",
        "display_max",
        "display_step",
        "display_origin",
        "display_unit",
        "quantization",
        "restoration",
    }
    missing = sorted(required - set(raw))
    if missing:
        if "display_unit" in missing:
            raise RigLoadError(f"{prefix}.display_unit must be a non-empty string")
        if "display_min" in missing and "display_max" in missing:
            raise RigLoadError(f"{prefix}.display_min and display_max are required")
        raise RigLoadError(f"{prefix} missing required key(s): {missing!r}")

    raw_min = int(_control_number(raw["raw_min"], f"{prefix}.raw_min", integer=True))
    raw_max = int(_control_number(raw["raw_max"], f"{prefix}.raw_max", integer=True))
    raw_step = int(_control_number(raw["raw_step"], f"{prefix}.raw_step", integer=True))
    raw_origin = int(
        _control_number(raw["raw_origin"], f"{prefix}.raw_origin", integer=True)
    )
    display_min = _control_decimal(raw["display_min"], f"{prefix}.display_min")
    display_max = _control_decimal(raw["display_max"], f"{prefix}.display_max")
    display_step = _control_decimal(raw["display_step"], f"{prefix}.display_step")
    display_origin = _control_decimal(raw["display_origin"], f"{prefix}.display_origin")
    display_unit = raw["display_unit"]
    if not isinstance(display_unit, str) or not display_unit.strip():
        raise RigLoadError(f"{prefix}.display_unit must be a non-empty string")
    for name, step in (("raw_step", raw_step), ("display_step", display_step)):
        if step <= 0:
            raise RigLoadError(f"{prefix}.{name} must be > 0")
    for name, value, origin, step in (
        ("raw_min", raw_min, raw_origin, raw_step),
        ("raw_max", raw_max, raw_origin, raw_step),
        ("display_min", display_min, display_origin, display_step),
        ("display_max", display_max, display_origin, display_step),
    ):
        if not _on_control_lattice(value, origin, step):
            raise RigLoadError(f"{prefix}.{name} must lie on its declared lattice")
    if not raw_min <= raw_origin <= raw_max:
        raise RigLoadError(f"{prefix}.raw_origin must be inside its declared range")
    if not display_min <= display_origin <= display_max:
        raise RigLoadError(f"{prefix}.display_origin must be inside its declared range")

    if "range_min" in raw and (
        raw["range_min"] != raw_min or raw["range_max"] != raw_max
    ):
        raise RigLoadError(f"{prefix} legacy range must equal explicit raw bounds")

    quantization = raw["quantization"]
    if (
        not isinstance(quantization, str)
        or quantization not in VALID_CONTROL_QUANTIZATION
    ):
        raise RigLoadError(
            f"{prefix}.quantization must be one of "
            f"{sorted(VALID_CONTROL_QUANTIZATION)!r}"
        )
    restoration = raw["restoration"]
    if not isinstance(restoration, str) or restoration not in VALID_CONTROL_RESTORATION:
        raise RigLoadError(
            f"{prefix}.restoration must be one of {sorted(VALID_CONTROL_RESTORATION)!r}"
        )

    lookup = None
    if mapping == "lookup":
        lookup = _parse_control_lookup(
            raw["lookup"],
            prefix,
            (raw_min, raw_max, raw_step, raw_origin),
            (display_min, display_max, display_step, display_origin),
            restoration,
        )
    if mapping == "identity":
        raw_domain = tuple(
            Decimal(value) for value in (raw_min, raw_max, raw_step, raw_origin)
        )
        display_domain = (display_min, display_max, display_step, display_origin)
        if raw_domain != display_domain:
            raise RigLoadError(f"{prefix} identity mapping requires identical domains")
    if mapping == "centered":
        if "raw_center" not in raw or "display_center" not in raw:
            raise RigLoadError(f"{prefix} centered mapping requires both center fields")
        raw_center = int(
            _control_number(raw["raw_center"], f"{prefix}.raw_center", integer=True)
        )
        display_center = _control_decimal(
            raw["display_center"], f"{prefix}.display_center"
        )
        if not raw_min <= raw_center <= raw_max:
            raise RigLoadError(f"{prefix}.raw_center must be inside its declared range")
        if not _on_control_lattice(raw_center, raw_origin, raw_step):
            raise RigLoadError(f"{prefix}.raw_center must lie on its declared lattice")
        if not display_min <= display_center <= display_max:
            raise RigLoadError(
                f"{prefix}.display_center must be inside its declared range"
            )
        if not _on_control_lattice(display_center, display_origin, display_step):
            raise RigLoadError(
                f"{prefix}.display_center must lie on its declared lattice"
            )
    elif "raw_center" in raw or "display_center" in raw:
        raise RigLoadError(f"{prefix} center fields require centered mapping")

    domain: _ScalarControlDomain = {
        "mapping": mapping,
        "raw_min": raw_min,
        "raw_max": raw_max,
        "raw_step": raw_step,
        "raw_origin": raw_origin,
        "display_min": display_min,
        "display_max": display_max,
        "display_step": display_step,
        "display_origin": display_origin,
        "display_unit": display_unit,
        "quantization": quantization,
        "restoration": restoration,
    }
    if mapping == "centered":
        domain["raw_center"] = raw_center
        domain["display_center"] = display_center
    if mapping == "lookup":
        assert lookup is not None
        domain["lookup"] = lookup
    public_spec: ControlSpec | None = {"style": style} if style is not None else None
    return public_spec, domain


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
    vfo_readback: str
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
    break_in_modes: tuple[int, ...] | None = None
    break_in_labels: dict[str, str] | None = None
    notch_width_values: tuple[int, ...] | None = None
    notch_width_labels: dict[str, str] | None = None
    ssb_tx_bw_values: tuple[int, ...] | None = None
    ssb_tx_bw_labels: dict[str, str] | None = None
    filter_shape_values: tuple[int, ...] | None = None
    filter_shape_labels: dict[str, str] | None = None
    # MOR-1447 leg 2: "separate" (default) or "combined" (Icom-style single
    # RF/SQL knob — see ``VALID_RF_SQL_CONTROL_MODELS``).
    rf_sql_control_model: str = "separate"
    filter_width_min: int = 50
    filter_width_max: int = 9999
    filter_width_encoding: str = "segmented_bcd_index"
    filter_config: dict[str, FilterWidthRule] | None = None
    max_watts: int | None = None
    data_mode_count: int = 0
    data_mode_labels: dict[str, str] | None = None
    protocol_type: str = "civ"
    protocol_address: int | None = None
    protocol_baud: int | None = None
    controls: dict[str, ControlSpec] | None = None
    _control_domains: dict[str, _ScalarControlDomain] | None = field(
        default=None, repr=False
    )
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
    # Stereo→mono RX downmix channel selection (MOR-508). "mix" = (L+R)//2
    # average (default, unchanged for every rig); "left"/"right" = that channel
    # at full level. The FTX-1 sets "left" (USB RX audio is on L only, so the
    # average with a silent R loses 6 dB).
    rx_audio_channel: str = "mix"
    write_only_controls: tuple[str, ...] = ()
    state_acquisition: RadioAcquisitionProfile | None = None
    tx_interlock_disposition_overrides: dict[
        TxInterlockCommandFamily, TxInterlockDisposition
    ] = field(default_factory=dict)
    tx_policy: TxPolicy = field(default_factory=TxPolicy)

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
        controls = cast(
            dict[str, ControlSpec | ControlDomainSpec] | None, self.controls
        )
        if self._control_domains:
            published_controls = cast(
                dict[str, ControlSpec | ControlDomainSpec],
                {name: spec.copy() for name, spec in (self.controls or {}).items()},
            )
            for name, domain in self._control_domains.items():
                if domain["mapping"] == "encoded":
                    published_encoded_domain: dict[str, object] = {
                        "mapping": "encoded",
                        "choices": [
                            (
                                cast(EncodedControlChoice, {"raw": raw, "label": value})
                                if isinstance(value, str)
                                else cast(
                                    EncodedControlChoice,
                                    {"raw": raw, "display": _public_decimal(value)},
                                )
                            )
                            for raw, value in cast(
                                tuple[_EncodedChoice, ...], domain["choices"]
                            )
                        ],
                    }
                    legacy = published_controls.get(name)
                    if legacy is not None and "style" in legacy:
                        published_encoded_domain["style"] = legacy["style"]
                    published_controls[name] = cast(
                        ControlDomainSpec, published_encoded_domain
                    )
                    continue
                published_domain: dict[str, object] = {
                    "mapping": cast(str, domain["mapping"]),
                    "raw_min": cast(int, domain["raw_min"]),
                    "raw_max": cast(int, domain["raw_max"]),
                    "raw_step": cast(int, domain["raw_step"]),
                    "raw_origin": cast(int, domain["raw_origin"]),
                    "display_min": _public_decimal(
                        cast(Decimal, domain["display_min"])
                    ),
                    "display_max": _public_decimal(
                        cast(Decimal, domain["display_max"])
                    ),
                    "display_step": _public_decimal(
                        cast(Decimal, domain["display_step"])
                    ),
                    "display_origin": _public_decimal(
                        cast(Decimal, domain["display_origin"])
                    ),
                    "display_unit": cast(str, domain["display_unit"]),
                    "quantization": cast(str, domain["quantization"]),
                    "restoration": cast(str, domain["restoration"]),
                }
                if published_domain["mapping"] == "centered":
                    published_domain["raw_center"] = cast(int, domain["raw_center"])
                    published_domain["display_center"] = _public_decimal(
                        cast(Decimal, domain["display_center"])
                    )
                if published_domain["mapping"] == "lookup":
                    published_domain["lookup"] = [
                        ControlLookupPoint(raw=raw, display=_public_decimal(display))
                        for raw, display in cast(
                            tuple[_LookupPoint, ...], domain["lookup"]
                        )
                    ]
                legacy = published_controls.get(name)
                if legacy is not None and "style" in legacy:
                    published_domain["style"] = legacy["style"]
                # The loader has already established the mapping-specific
                # shape above; this cast keeps the public discriminated union
                # explicit without weakening its legacy counterpart.
                published_controls[name] = cast(ControlDomainSpec, published_domain)
            controls = published_controls

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
            vfo_readback=self.vfo_readback,
            has_lan=self.has_lan,
            freq_ranges=ranges,
            modes=tuple(self.modes),
            filters=tuple(self.filters),
            command_names=frozenset(
                name
                for name, spec in self.commands.items()
                if not isinstance(spec, AbsentCommandSpec)
            ),
            absent_command_names=frozenset(
                name
                for name, spec in self.commands.items()
                if isinstance(spec, AbsentCommandSpec)
            ),
            command_map=self.to_command_map(),
            filter_width_min=self.filter_width_min,
            filter_width_max=self.filter_width_max,
            filter_width_encoding=self.filter_width_encoding,
            filter_config=self.filter_config,
            max_watts=self.max_watts,
            att_values=self.att_values,
            att_labels=self.att_labels,
            pre_values=self.pre_values,
            pre_labels=self.pre_labels,
            agc_modes=self.agc_modes,
            agc_labels=self.agc_labels,
            break_in_modes=self.break_in_modes,
            break_in_labels=self.break_in_labels,
            notch_width_values=self.notch_width_values,
            notch_width_labels=self.notch_width_labels,
            ssb_tx_bw_values=self.ssb_tx_bw_values,
            ssb_tx_bw_labels=self.ssb_tx_bw_labels,
            filter_shape_values=self.filter_shape_values,
            filter_shape_labels=self.filter_shape_labels,
            rf_sql_control_model=self.rf_sql_control_model,
            data_mode_count=self.data_mode_count,
            data_mode_labels=self.data_mode_labels,
            # isinstance, not membership: a declared-absent entry
            # (AbsentCommandSpec, MOR-2005 step 4a) is a dict key too, but
            # it means the opposite of "the radio has this command" — see
            # `tests/test_rig_loader.py:
            # TestSetModeViaSelectedDiscriminatesAbsent`. CivCommandSpec
            # specifically (not CatCommandSpec) because this flag gates a
            # CI-V-only wire path (`runtime/_dual_rx_runtime.py:
            # DualRxRuntimeMixin._set_mode_main`'s CI-V 0x26 0x00 branch).
            set_mode_via_selected=isinstance(
                self.commands.get("set_selected_mode"), CivCommandSpec
            ),
            protocol_type=self.protocol_type,
            hamlib_model_id=self.hamlib_model_id,
            controls=controls,
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
            write_only_controls=frozenset(self.write_only_controls),
            state_acquisition=self.state_acquisition,
            tx_interlock_disposition_overrides=self.tx_interlock_disposition_overrides,
            tx_policy=self.tx_policy,
        )

    def to_command_map(self) -> CommandMap:
        """Build a ``CommandMap`` from this config's CI-V commands.

        Only CivCommandSpec entries are included; CatCommandSpec and
        AbsentCommandSpec entries are excluded (pinned by
        `tests/test_rig_loader.py: TestAbsentCommandSemantics
        .test_absent_name_excluded_from_command_map`).
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

    Supports three formats:
    1. CI-V wire bytes (list): the frame's full constant prefix, per the
       tuple contract ruled in Q7
       (`docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1) —
       command, sub-command, and any further constant bytes (extended menu
       addressing, a selector byte, or a constant payload byte). Examples:
       [0x03] (command only), [0x14, 0x01] (command + sub), or
       [0x1C, 0x00, 0x01] (command + sub + a constant payload byte, the
       X6100 ptt_on shape).
    2. CAT command spec (dict): { cat = { read = "FA;", parse = "FA{freq:09d};" } }
    3. Declared-absent (dict, MOR-2005 step 4a): { absent = "<source>" } —
       this radio confirmed does not have the command, per the named
       authority. See `commands/command_spec.py: AbsentCommandSpec`.

    Args:
        filename: Source TOML filename (for error messages).
        command_name: Command name (for error messages).
        value: Raw TOML value to parse.

    Returns:
        Parsed CommandSpec (CivCommandSpec, CatCommandSpec, or
        AbsentCommandSpec).

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

    # Format 3: declared-absent (dict with 'absent' key, MOR-2005 step 4a)
    if isinstance(value, dict) and "absent" in value:
        extra_keys = sorted(set(value) - {"absent"})
        if extra_keys:
            raise RigLoadError(
                f"{filename}: [commands].{command_name} = {{ absent = ... }} "
                f"must not have other keys, got extra: {extra_keys}"
            )
        source = value["absent"]
        if not isinstance(source, str) or not source.strip():
            raise RigLoadError(
                f"{filename}: [commands].{command_name}.absent must be a "
                f"non-empty string naming the authority (a manual, a wfview "
                f"rig definition, ...), got {source!r}"
            )
        return AbsentCommandSpec(source=source)

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


def _filter_undeclared_mode_bindings(
    keyboard: KeyboardConfig | None,
    modes: tuple[str, ...],
) -> KeyboardConfig | None:
    """Exclude mode shortcuts that do not target a declared profile mode."""
    if keyboard is None:
        return None

    bindings = tuple(
        binding
        for binding in keyboard.bindings
        if not (
            binding.action == "mode_select"
            and isinstance(binding.params, dict)
            and isinstance(binding.params.get("mode"), str)
            and binding.params["mode"] not in modes
        )
    )
    if len(bindings) == len(keyboard.bindings):
        return keyboard
    return KeyboardConfig(
        leader_key=keyboard.leader_key,
        leader_timeout_ms=keyboard.leader_timeout_ms,
        alt_hints=keyboard.alt_hints,
        help_title=keyboard.help_title,
        bindings=bindings,
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


def _state_path_list(filename: str, section: str, value: Any) -> tuple[FieldPath, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RigLoadError(f"{filename}: {section} must be a list of field paths")
    try:
        return tuple(FieldPath.parse(item) for item in value)
    except ValueError as exc:
        raise RigLoadError(
            f"{filename}: {section} has invalid field path: {exc}"
        ) from exc


def _reject_unknown_keys(
    filename: str,
    section: str,
    raw: dict[str, Any],
    allowed: frozenset[str],
) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise RigLoadError(
            f"{filename}: {section} unknown key {unknown[0]!r}. "
            f"Valid keys: {sorted(allowed)}"
        )


def _parse_enumerated_domain(
    filename: str,
    section: str,
    raw_section: dict[str, Any],
    *,
    values_key: str = "values",
    labels_key: str = "labels",
) -> tuple[tuple[int, ...] | None, dict[str, str] | None]:
    """Parse a ``[section] <values_key>/<labels_key>`` enumerated-domain
    declaration, generalizing the ``[agc] modes/labels`` fail-loud pattern
    (MOR-1522) to the other per-radio enumerated controls (MOR-1534:
    break_in, notch width, ssb_tx_bw, filter_shape). Rejects unknown keys,
    non-integer/empty value lists, orphan label keys, and — the MOR-1522 R1
    (B2) hole class — labels declared without a matching values list, which
    would otherwise load silently as a capability-present control with an
    empty domain.
    """
    if raw_section:
        _reject_unknown_keys(
            filename, section, raw_section, frozenset({values_key, labels_key})
        )
    values = tuple(raw_section[values_key]) if values_key in raw_section else None
    if values is not None and (
        not values or any(isinstance(v, bool) or not isinstance(v, int) for v in values)
    ):
        raise RigLoadError(
            f"{filename}: {section}.{values_key} must be a non-empty list of integers"
        )
    labels = dict(raw_section[labels_key]) if labels_key in raw_section else None
    if labels is not None and values is None:
        raise RigLoadError(
            f"{filename}: {section}.{labels_key} declared without {section}.{values_key}"
        )
    if labels is not None and values is not None:
        declared = {str(v) for v in values}
        orphan_labels = sorted(set(labels) - declared)
        if orphan_labels:
            raise RigLoadError(
                f"{filename}: {section}.{labels_key} key {orphan_labels[0]!r} has no "
                f"matching entry in {section}.{values_key} {sorted(values)}"
            )
    return values, labels


def _strict_policy_bool(
    filename: str,
    prefix: str,
    key: str,
    value: Any,
) -> bool:
    if not isinstance(value, bool):
        raise RigLoadError(f"{filename}: {prefix}.{key} must be a bool")
    return value


def _strict_policy_float(
    filename: str,
    prefix: str,
    key: str,
    value: Any,
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RigLoadError(f"{filename}: {prefix}.{key} must be a number")
    return float(value)


def _policy_seconds(
    filename: str,
    prefix: str,
    raw: dict[str, Any],
    key: str,
    *,
    defaults: AcquisitionPolicy | None,
    fallback: float,
    label: str | None = None,
) -> float | None:
    if key in raw:
        value = raw[key]
    elif defaults is not None:
        value = getattr(defaults, key)
    else:
        value = fallback
    key_label = label if label is not None else key
    return (
        None
        if value is None
        else _strict_policy_float(filename, prefix, key_label, value)
    )


_ACQUISITION_POLICY_KEYS = frozenset(
    {
        "cadence_seconds",
        "freshness_ttl_seconds",
        "reconciliation_priority",
        "adaptive_decay",
        "adaptive_decay_idle_multiplier",
        "adaptive_decay_max_cadence_seconds",
        "external_cat_pause",
        "meter_coalescing_window_seconds",
        "tx_only",
    }
)

_STATE_ACQUISITION_KEYS = frozenset(
    {
        "provider",
        "default_cadence_seconds",
        "default_freshness_ttl_seconds",
        "default_reconciliation_priority",
        "adaptive_decay",
        "adaptive_decay_idle_multiplier",
        "adaptive_decay_max_cadence_seconds",
        "external_cat_pause",
        "meter_coalescing_window_seconds",
        "capabilities",
        "field_policies",
    }
)

_STATE_ACQUISITION_CAPABILITY_KEYS = frozenset(
    {
        "unsolicited_push",
        "polling_only",
        "stream_like_meters",
        "command_response_observable",
        "supported_controls",
        "unsupported",
        "unknown",
    }
)


def _parse_acquisition_policy(
    filename: str,
    raw: dict[str, Any],
    *,
    prefix: str,
    defaults: AcquisitionPolicy | None = None,
    key_labels: dict[str, str] | None = None,
) -> AcquisitionPolicy:
    _reject_unknown_keys(filename, prefix, raw, _ACQUISITION_POLICY_KEYS)
    labels = key_labels or {}
    try:
        return AcquisitionPolicy(
            cadence_seconds=_policy_seconds(
                filename,
                prefix,
                raw,
                "cadence_seconds",
                defaults=defaults,
                fallback=5.0,
                label=labels.get("cadence_seconds"),
            ),
            freshness_ttl_seconds=_policy_seconds(
                filename,
                prefix,
                raw,
                "freshness_ttl_seconds",
                defaults=defaults,
                fallback=15.0,
                label=labels.get("freshness_ttl_seconds"),
            ),
            reconciliation_priority=ReconciliationPriority(
                str(
                    raw.get(
                        "reconciliation_priority",
                        defaults.reconciliation_priority
                        if defaults is not None
                        else ReconciliationPriority.POLL,
                    )
                )
            ),
            adaptive_decay=AdaptiveDecayPolicy(
                enabled=_strict_policy_bool(
                    filename,
                    prefix,
                    labels.get("adaptive_decay", "adaptive_decay"),
                    raw.get(
                        "adaptive_decay",
                        defaults.adaptive_decay.enabled
                        if defaults is not None
                        else False,
                    ),
                ),
                idle_multiplier=_strict_policy_float(
                    filename,
                    prefix,
                    labels.get(
                        "adaptive_decay_idle_multiplier",
                        "adaptive_decay_idle_multiplier",
                    ),
                    raw.get(
                        "adaptive_decay_idle_multiplier",
                        defaults.adaptive_decay.idle_multiplier
                        if defaults is not None
                        else 1.0,
                    ),
                ),
                max_cadence_seconds=(
                    None
                    if raw.get(
                        "adaptive_decay_max_cadence_seconds",
                        defaults.adaptive_decay.max_cadence_seconds
                        if defaults is not None
                        else None,
                    )
                    is None
                    else _strict_policy_float(
                        filename,
                        prefix,
                        labels.get(
                            "adaptive_decay_max_cadence_seconds",
                            "adaptive_decay_max_cadence_seconds",
                        ),
                        raw.get(
                            "adaptive_decay_max_cadence_seconds",
                            defaults.adaptive_decay.max_cadence_seconds
                            if defaults is not None
                            else None,
                        ),
                    )
                ),
            ),
            external_cat_pause=ExternalCatPauseBehavior(
                str(
                    raw.get(
                        "external_cat_pause",
                        defaults.external_cat_pause
                        if defaults is not None
                        else ExternalCatPauseBehavior.PAUSE_POLLING,
                    )
                )
            ),
            meter_coalescing=(
                MeterCoalescingPolicy(
                    window_seconds=_strict_policy_float(
                        filename,
                        prefix,
                        labels.get(
                            "meter_coalescing_window_seconds",
                            "meter_coalescing_window_seconds",
                        ),
                        raw["meter_coalescing_window_seconds"],
                    )
                )
                if "meter_coalescing_window_seconds" in raw
                else None
            ),
            tx_only=_strict_policy_bool(
                filename,
                prefix,
                labels.get("tx_only", "tx_only"),
                raw.get(
                    "tx_only",
                    defaults.tx_only if defaults is not None else False,
                ),
            ),
        )
    except (TypeError, ValueError) as exc:
        raise RigLoadError(
            f"{filename}: {prefix} invalid acquisition policy: {exc}"
        ) from exc


def _parse_state_acquisition(
    filename: str,
    raw: Any,
) -> RadioAcquisitionProfile | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RigLoadError(f"{filename}: [state_acquisition] must be a table")
    _reject_unknown_keys(
        filename,
        "[state_acquisition]",
        raw,
        _STATE_ACQUISITION_KEYS,
    )

    caps_raw = raw.get("capabilities", {})
    if not isinstance(caps_raw, dict):
        raise RigLoadError(
            f"{filename}: [state_acquisition.capabilities] must be a table"
        )
    _reject_unknown_keys(
        filename,
        "[state_acquisition.capabilities]",
        caps_raw,
        _STATE_ACQUISITION_CAPABILITY_KEYS,
    )

    section = "[state_acquisition.capabilities]"
    unsolicited = set(
        _state_path_list(
            filename, f"{section}.unsolicited_push", caps_raw.get("unsolicited_push")
        )
    )
    polling = set(
        _state_path_list(
            filename, f"{section}.polling_only", caps_raw.get("polling_only")
        )
    )
    stream = set(
        _state_path_list(
            filename,
            f"{section}.stream_like_meters",
            caps_raw.get("stream_like_meters"),
        )
    )
    command_response = set(
        _state_path_list(
            filename,
            f"{section}.command_response_observable",
            caps_raw.get("command_response_observable"),
        )
    )
    supported_controls = set(
        _state_path_list(
            filename,
            f"{section}.supported_controls",
            caps_raw.get("supported_controls"),
        )
    )
    unsupported = set(
        _state_path_list(
            filename, f"{section}.unsupported", caps_raw.get("unsupported")
        )
    )
    unknown = set(
        _state_path_list(filename, f"{section}.unknown", caps_raw.get("unknown"))
    )

    paths = (
        unsolicited
        | polling
        | stream
        | command_response
        | supported_controls
        | unsupported
        | unknown
    )
    capabilities: list[FieldCapability] = []
    for path in sorted(paths, key=str):
        availability = FieldAvailability.SUPPORTED
        diagnostic = ""
        if path in unsupported:
            availability = FieldAvailability.UNSUPPORTED
            diagnostic = "profile marks field unsupported"
        if path in unknown:
            availability = FieldAvailability.UNKNOWN
            diagnostic = "profile marks field unknown"
        try:
            capabilities.append(
                FieldCapability(
                    path=path,
                    availability=availability,
                    unsolicited_push=path in unsolicited,
                    polling=path in polling or path in stream,
                    stream_like=path in stream,
                    command_response_observable=path in command_response,
                    supported_controls=(
                        ("profile_control",) if path in supported_controls else ()
                    ),
                    diagnostic=diagnostic,
                )
            )
        except ValueError as exc:
            raise RigLoadError(f"{filename}: {path}: {exc}") from exc

    default_policy = _parse_acquisition_policy(
        filename,
        {
            "cadence_seconds": raw.get("default_cadence_seconds", 5.0),
            "freshness_ttl_seconds": raw.get("default_freshness_ttl_seconds", 15.0),
            "reconciliation_priority": raw.get(
                "default_reconciliation_priority",
                ReconciliationPriority.POLL,
            ),
            "adaptive_decay": raw.get("adaptive_decay", False),
            "adaptive_decay_idle_multiplier": raw.get(
                "adaptive_decay_idle_multiplier",
                1.0,
            ),
            "adaptive_decay_max_cadence_seconds": raw.get(
                "adaptive_decay_max_cadence_seconds"
            ),
            "external_cat_pause": raw.get(
                "external_cat_pause",
                ExternalCatPauseBehavior.PAUSE_POLLING,
            ),
            **(
                {
                    "meter_coalescing_window_seconds": raw[
                        "meter_coalescing_window_seconds"
                    ]
                }
                if "meter_coalescing_window_seconds" in raw
                else {}
            ),
        },
        prefix="[state_acquisition]",
        key_labels={
            "cadence_seconds": "default_cadence_seconds",
            "freshness_ttl_seconds": "default_freshness_ttl_seconds",
        },
    )

    policies_raw = raw.get("field_policies", {})
    if not isinstance(policies_raw, dict):
        raise RigLoadError(
            f"{filename}: [state_acquisition.field_policies] must be a table"
        )
    field_policies: dict[FieldPath, AcquisitionPolicy] = {}
    for path_text, policy_raw in policies_raw.items():
        if not isinstance(policy_raw, dict):
            raise RigLoadError(
                f"{filename}: [state_acquisition.field_policies.{path_text}] must be a table"
            )
        try:
            path = FieldPath.parse(str(path_text))
        except ValueError as exc:
            raise RigLoadError(
                f"{filename}: [state_acquisition.field_policies] invalid path {path_text!r}: {exc}"
            ) from exc
        field_policies[path] = _parse_acquisition_policy(
            filename,
            policy_raw,
            prefix=f"[state_acquisition.field_policies.{path_text}]",
            defaults=default_policy,
        )

    provider = raw.get("provider", "profile")
    if not isinstance(provider, str):
        raise RigLoadError(f"{filename}: [state_acquisition].provider must be a string")

    try:
        return RadioAcquisitionProfile(
            provider=provider,
            capabilities=tuple(capabilities),
            default_policy=default_policy,
            field_policies=field_policies,
        )
    except ValueError as exc:
        raise RigLoadError(f"{filename}: [state_acquisition] invalid: {exc}") from exc


_TX_INTERLOCK_METADATA_BY_FAMILY = {
    metadata.family: metadata for metadata in TX_INTERLOCK_COMMAND_FAMILY_METADATA
}


def _toml_shape_statements(source: str) -> list[list[tuple[str, str]]]:
    """Expose only table/key punctuation while shielding strings and comments."""

    statements: list[list[tuple[str, str]]] = []
    statement: list[tuple[str, str]] = []
    punctuation = "[]{}.="
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\n":
            if statement:
                statements.append(statement)
                statement = []
            index += 1
            continue
        if char in " \t\r":
            index += 1
            continue
        if char == "#":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline
            continue
        if source.startswith(('"""', "'''"), index):
            delimiter = source[index : index + 3]
            index += 3
            while index < len(source) and not source.startswith(delimiter, index):
                if delimiter == '"""' and source[index] == "\\":
                    index += 2
                else:
                    index += 1
            index += 3
            for _ in range(2):
                if index < len(source) and source[index] == delimiter[0]:
                    index += 1
            statement.append(("string", ""))
            continue
        if char in "\"'":
            delimiter = char
            start = index
            index += 1
            while index < len(source) and source[index] != delimiter:
                if delimiter == '"' and source[index] == "\\":
                    index += 2
                else:
                    index += 1
            index += 1
            literal = source[start:index]
            value = tomllib.loads(f"key = {literal}")["key"]
            statement.append(("key", value))
            continue
        if char in punctuation:
            statement.append((char, char))
            index += 1
            continue
        start = index
        while index < len(source) and source[index] not in f" \t\r\n#{punctuation}\"'":
            index += 1
        statement.append(("key", source[start:index]))
    if statement:
        statements.append(statement)
    return statements


def _toml_key_path(tokens: list[tuple[str, str]]) -> tuple[str, ...] | None:
    """Return a dotted key path, or ``None`` for tokens outside that shape."""

    path: list[str] = []
    expect_key = True
    for kind, value in tokens:
        if expect_key and kind == "key":
            path.append(value)
            expect_key = False
        elif not expect_key and kind == ".":
            expect_key = True
        else:
            return None
    return tuple(path) if path and not expect_key else None


def _validate_tx_interlock_override_syntax(filename: str, source: str) -> None:
    """Require the documented table plus one non-dotted inline mapping key."""

    current_table: tuple[str, ...] = ()
    forbidden_prefix = ("tx_interlock", "disposition_overrides")
    container_depth = 0
    for tokens in _toml_shape_statements(source):
        if container_depth == 0 and tokens[0][0] == "[" and tokens[-1][0] == "]":
            inner = tokens[1:-1]
            if inner and inner[0][0] == "[" and inner[-1][0] == "]":
                inner = inner[1:-1]
            path = _toml_key_path(inner)
            current_table = path or ()
            if current_table[:2] == forbidden_prefix:
                raise RigLoadError(
                    f"{filename}: [tx_interlock].disposition_overrides "
                    "must use inline table syntax"
                )
            continue

        if container_depth == 0:
            equals = next(
                (position for position, token in enumerate(tokens) if token[0] == "="),
                None,
            )
            key_path = _toml_key_path(tokens[:equals]) if equals is not None else None
            if key_path is not None:
                assert equals is not None
                dotted_in_table = (
                    current_table == ("tx_interlock",)
                    and key_path[:1] == ("disposition_overrides",)
                    and len(key_path) > 1
                )
                dotted_at_root = (
                    current_table == () and key_path[:2] == forbidden_prefix
                )
                outer_inline = (
                    current_table == ()
                    and key_path == ("tx_interlock",)
                    and tokens[equals + 1][0] == "{"
                )
                if dotted_in_table or dotted_at_root or outer_inline:
                    raise RigLoadError(
                        f"{filename}: [tx_interlock].disposition_overrides "
                        "must use inline table syntax"
                    )
        container_depth += sum(token[0] in "[{" for token in tokens)
        container_depth -= sum(token[0] in "]}" for token in tokens)


def _parse_tx_interlock_disposition_overrides(
    filename: str, raw: object
) -> dict[TxInterlockCommandFamily, TxInterlockDisposition]:
    """Validate the profile-only, one-way TX interlock tightening mapping."""

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RigLoadError(f"{filename}: [tx_interlock] must be a table")

    unknown_keys = set(raw) - {"disposition_overrides"}
    if unknown_keys:
        raise RigLoadError(
            f"{filename}: [tx_interlock] unknown key(s): {sorted(unknown_keys)}"
        )

    overrides_raw = raw.get("disposition_overrides", {})
    if not isinstance(overrides_raw, dict):
        raise RigLoadError(
            f"{filename}: [tx_interlock].disposition_overrides must be an inline table"
        )

    overrides: dict[TxInterlockCommandFamily, TxInterlockDisposition] = {}
    for family_value, disposition_value in overrides_raw.items():
        try:
            family = TxInterlockCommandFamily(family_value)
        except ValueError as exc:
            raise RigLoadError(
                f"{filename}: [tx_interlock].disposition_overrides has unknown "
                f"command family {family_value!r}"
            ) from exc

        if not isinstance(disposition_value, str):
            raise RigLoadError(
                f"{filename}: [tx_interlock].disposition_overrides[{family_value!r}] "
                "must be a string"
            )
        if disposition_value != TxInterlockDisposition.DEFER.value:
            raise RigLoadError(
                f"{filename}: [tx_interlock].disposition_overrides[{family_value!r}] "
                "must be 'defer'"
            )

        metadata = _TX_INTERLOCK_METADATA_BY_FAMILY[family]
        if metadata.base_disposition is not TxInterlockDisposition.TX_SAFE:
            raise RigLoadError(
                f"{filename}: [tx_interlock].disposition_overrides family "
                f"{family_value!r} has base disposition "
                f"{metadata.base_disposition.value!r}, not tx-safe"
            )
        overrides[family] = TxInterlockDisposition.DEFER

    return overrides


_TX_POLICY_KEYS = frozenset({"refused_during_tx", "tx_state_map"})


def _parse_tx_policy(filename: str, raw: Any) -> TxPolicy:
    """Parse the measured per-radio ``[tx_policy]`` section (MOR-1912).

    ``refused_during_tx`` entries are validated for shape only — a list of
    unique, non-empty strings — never against a fixed vocabulary. The
    command-family vocabulary's single source of truth is
    ``core/tx_authority.py``, which is not yet on ``main``; duplicating its
    membership list here would create a second copy with no mechanism
    keeping it in step with the first.
    """
    if raw is None:
        return TxPolicy()
    if not isinstance(raw, dict):
        raise RigLoadError(f"{filename}: [tx_policy] must be a table")
    _reject_unknown_keys(filename, "[tx_policy]", raw, _TX_POLICY_KEYS)

    refused_raw = raw.get("refused_during_tx", [])
    if not isinstance(refused_raw, list):
        raise RigLoadError(f"{filename}: [tx_policy].refused_during_tx must be a list")
    refused: list[str] = []
    for entry in refused_raw:
        if not isinstance(entry, str) or not entry:
            raise RigLoadError(
                f"{filename}: [tx_policy].refused_during_tx entries must be "
                "non-empty strings"
            )
        refused.append(entry)
    if len(refused) != len(set(refused)):
        raise RigLoadError(
            f"{filename}: [tx_policy].refused_during_tx must not contain duplicates"
        )

    state_map_raw = raw.get("tx_state_map", {})
    if not isinstance(state_map_raw, dict):
        raise RigLoadError(f"{filename}: [tx_policy].tx_state_map must be a table")
    tx_state_map: dict[str, str] = {}
    for key, value in state_map_raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RigLoadError(
                f"{filename}: [tx_policy].tx_state_map must map strings to strings"
            )
        tx_state_map[key] = value

    return TxPolicy(refused_during_tx=frozenset(refused), tx_state_map=tx_state_map)


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
        source = raw.decode()
        data = tomllib.loads(source)
    except Exception as exc:
        raise RigLoadError(f"{filename}: failed to parse TOML: {exc}") from exc

    _validate_tx_interlock_override_syntax(filename, source)

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
    rf_sql_control_model = data["capabilities"].get("rf_sql_control_model", "separate")
    if rf_sql_control_model not in VALID_RF_SQL_CONTROL_MODELS:
        raise RigLoadError(
            f"{filename}: [capabilities].rf_sql_control_model must be one of "
            f"{sorted(VALID_RF_SQL_CONTROL_MODELS)}, got {rf_sql_control_model!r}"
        )

    # Validate [validation].write_only_controls — each entry must be a declared
    # capability. These route through the validate set-and-observe engine path
    # (MOR-208) instead of read-modify-verify-restore.
    feature_set = set(features)
    write_only_raw = data.get("validation", {}).get("write_only_controls", [])
    for c in write_only_raw:
        if c not in feature_set:
            raise RigLoadError(
                f"{filename}: [validation].write_only_controls entry {c!r} "
                f"is not a declared capability"
            )
    write_only_controls = tuple(write_only_raw)

    # Validate [vfo]
    vfo = data["vfo"]
    scheme = vfo.get("scheme", "")
    if scheme not in VALID_VFO_SCHEMES:
        raise RigLoadError(
            f"{filename}: [vfo].scheme must be one of {VALID_VFO_SCHEMES}, "
            f"got {scheme!r}"
        )
    vfo_readback = vfo.get("readback", "none")
    if vfo_readback not in VALID_VFO_READBACK:
        raise RigLoadError(
            f"{filename}: [vfo].readback must be one of "
            f"{sorted(VALID_VFO_READBACK)}, got {vfo_readback!r}"
        )
    expected_receiver_count = 1 if scheme in {"single", "ab"} else 2
    receiver_count = radio["receiver_count"]
    if (
        not isinstance(receiver_count, int)
        or isinstance(receiver_count, bool)
        or receiver_count != expected_receiver_count
    ):
        raise RigLoadError(
            f"{filename}: [radio].receiver_count = {receiver_count!r} is incompatible "
            f"with [vfo].scheme = {scheme!r}; expected {expected_receiver_count}"
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

    agc_modes, agc_labels = _parse_enumerated_domain(
        filename, "[agc]", data.get("agc", {}), values_key="modes"
    )

    # Parse break_in/notch-width/ssb_tx_bw/filter_shape enumerated domains
    # (MOR-1534). Each was declared in TOML (or, for filter_shape, nowhere
    # at all) but never parsed/validated at load time — see rig_loader
    # tests + CoreRadio's setters for the per-control validation seat.
    break_in_modes, break_in_labels = _parse_enumerated_domain(
        filename, "[break_in]", data.get("break_in", {})
    )
    notch_width_values, notch_width_labels = _parse_enumerated_domain(
        filename,
        "[notch]",
        data.get("notch", {}),
        values_key="width_values",
        labels_key="width_labels",
    )
    ssb_tx_bw_values, ssb_tx_bw_labels = _parse_enumerated_domain(
        filename, "[ssb_tx_bw]", data.get("ssb_tx_bw", {})
    )
    filter_shape_values, filter_shape_labels = _parse_enumerated_domain(
        filename, "[filter_shape]", data.get("filter_shape", {})
    )

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
    control_domains: dict[str, _ScalarControlDomain] | None = None
    if controls_raw is not None:
        if not isinstance(controls_raw, dict):
            raise RigLoadError(f"{filename}: [controls] must be a table")
        controls = {}
        control_domains = {}
        for ctrl_name, ctrl_data in controls_raw.items():
            public_spec, domain = _parse_control_spec(filename, ctrl_name, ctrl_data)
            if public_spec is not None:
                controls[ctrl_name] = public_spec
            if domain is not None:
                control_domains[ctrl_name] = domain
        if not controls and control_domains:
            controls = None
        if not control_domains:
            control_domains = None

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
        rules.append(cast(RuleSpec, dict(rule)))

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
    keyboard = _filter_undeclared_mode_bindings(keyboard, modes)

    # Parse optional [audio] codec and sample-rate policy (#797, #1470).
    codec_preference: tuple[str, ...] | None = None
    tx_codec: str | None = None
    default_sample_rate_hz: int | None = None
    supported_sample_rates_hz: tuple[int, ...] | None = None
    sample_rate_by_codec: dict[str, int] | None = None
    browser_rx_transport: str | None = None
    browser_rx_transcode_to_opus: bool | None = None
    rx_audio_channel: str = "mix"
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
        if "rx_audio_channel" in audio_section:
            rx_channel_raw = audio_section["rx_audio_channel"]
            if (
                not isinstance(rx_channel_raw, str)
                or rx_channel_raw not in VALID_RX_AUDIO_CHANNELS
            ):
                raise RigLoadError(
                    f"{filename}: [audio].rx_audio_channel must be one of "
                    f"{sorted(VALID_RX_AUDIO_CHANNELS)}, got {rx_channel_raw!r}"
                )
            rx_audio_channel = rx_channel_raw

    max_watts: int | None = None
    power_section = data.get("power")
    if power_section is not None:
        if not isinstance(power_section, dict):
            raise RigLoadError(f"{filename}: [power] must be a table")
        if "max_watts" in power_section:
            max_watts_raw = power_section["max_watts"]
            if not isinstance(max_watts_raw, int) or isinstance(max_watts_raw, bool):
                raise RigLoadError(f"{filename}: [power].max_watts must be an integer")
            if max_watts_raw <= 0:
                raise RigLoadError(f"{filename}: [power].max_watts must be > 0")
            max_watts = max_watts_raw

    state_acquisition = _parse_state_acquisition(
        filename,
        data.get("state_acquisition"),
    )
    tx_interlock_disposition_overrides = _parse_tx_interlock_disposition_overrides(
        filename,
        data.get("tx_interlock"),
    )
    tx_policy = _parse_tx_policy(filename, data.get("tx_policy"))

    return RigConfig(
        id=radio["id"],
        model=radio["model"],
        civ_addr=civ_addr,
        receiver_count=receiver_count,
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
        max_watts=max_watts,
        vfo_scheme=scheme,
        vfo_readback=vfo_readback,
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
        break_in_modes=break_in_modes,
        break_in_labels=break_in_labels,
        notch_width_values=notch_width_values,
        notch_width_labels=notch_width_labels,
        ssb_tx_bw_values=ssb_tx_bw_values,
        ssb_tx_bw_labels=ssb_tx_bw_labels,
        filter_shape_values=filter_shape_values,
        filter_shape_labels=filter_shape_labels,
        rf_sql_control_model=rf_sql_control_model,
        data_mode_count=data_mode_count,
        data_mode_labels=data_mode_labels,
        protocol_type=protocol_type,
        protocol_address=protocol_address,
        protocol_baud=protocol_baud,
        controls=controls,
        _control_domains=control_domains,
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
        rx_audio_channel=rx_audio_channel,
        write_only_controls=write_only_controls,
        state_acquisition=state_acquisition,
        tx_interlock_disposition_overrides=tx_interlock_disposition_overrides,
        tx_policy=tx_policy,
    )


def discover_rigs(directory: Path) -> dict[str, RigConfig]:
    """Discover and load all rig TOML files in a directory.

    Files starting with underscore are ignored (e.g. ``_schema.md``,
    ``_template.toml``). ``*.draft.toml`` files (emitted by ``rigplane
    convert``) are also ignored so an unreviewed bootstrap draft is never
    auto-loaded as a real profile.

    Returns:
        Dict mapping model name to ``RigConfig``.
    """
    rigs: dict[str, RigConfig] = {}
    if not directory.is_dir():
        return rigs

    for path in sorted(directory.glob("*.toml")):
        if path.name.startswith("_") or path.name.endswith(".draft.toml"):
            continue
        rig = load_rig(path)
        rigs[rig.model] = rig

    return rigs


def _discover_resource_rigs(directory: Traversable) -> dict[str, RigConfig]:
    """Load profiles from a package-resource directory.

    Filesystem resources can use the normal loader directly. Non-filesystem
    resources (for example, a zip import) are materialized together so profile
    includes such as ``_keyboard-default.toml`` remain available as siblings.
    """
    if not directory.is_dir():
        return {}
    if isinstance(directory, Path):
        return discover_rigs(directory)

    with tempfile.TemporaryDirectory(prefix="rigplane-rigs-") as temp_dir:
        materialized = Path(temp_dir)
        for resource in directory.iterdir():
            if resource.is_file() and resource.name.endswith(".toml"):
                (materialized / resource.name).write_bytes(resource.read_bytes())
        return discover_rigs(materialized)


def discover_available_rigs(
    fallback_directory: Path | None = None,
) -> dict[str, RigConfig]:
    """Discover bundled profiles, with an optional filesystem fallback.

    Package resources are authoritative for installed distributions. The
    fallback preserves source/editable checkouts whose profiles live at the
    repository root.
    """
    package_rigs = resources.files("rigplane").joinpath("rigs")
    rigs = _discover_resource_rigs(package_rigs)
    if rigs or fallback_directory is None:
        return rigs
    return discover_rigs(fallback_directory)
