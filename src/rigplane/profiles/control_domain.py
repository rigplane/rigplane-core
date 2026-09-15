"""Exact control-domain math shared by every RigPlane consumer.

This module is the Python half of one mechanism for control-domain
arithmetic (MOR-2472): decode raw→display, quantize a display value onto
the lattice, encode display→raw, and validate raw values, operating on
the *published* normalized domain mapping (canonical decimal strings,
exactly what the frontend receives). It mirrors
``frontend/src/lib/radio/control-domain.ts`` function for function and
must stay semantically identical to it.

All arithmetic is exact: decimals are parsed from canonical strings only
(never constructed from floats) and computed as integer
``(coefficient, scale)`` pairs — the Python analogue of the TypeScript
bigint representation — so precision never depends on a decimal context.
Malformed domains and inputs fail closed to ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import cast

__all__ = [
    "decode_control_domain",
    "encode_control_domain",
    "quantize_control_domain",
    "snap_control_domain",
    "validate_control_raw_value",
]

_QUANTIZATIONS = frozenset(
    {"nearest_ties_down", "nearest_ties_up", "floor", "ceil", "reject"}
)
# ``\Z`` (not ``$``) so a trailing newline can never sneak past the end
# anchor; ``$`` would accept ``'0\n'`` and break parity with the
# TypeScript mirror, whose ``$`` is a true end-of-input anchor.
_CANONICAL_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z")
# Parity with the TypeScript ``Number.isSafeInteger`` guards: values the
# frontend cannot represent exactly are rejected rather than mis-mapped.
_SAFE_INTEGER_MAX = 2**53 - 1

# Exact decimal as (coefficient, scale) with coefficient * 10**-scale.
_Decimal = tuple[int, int]
_Axis = tuple[_Decimal, _Decimal, _Decimal, _Decimal]
_LookupPoint = tuple[int, str, _Decimal]


def _public_decimal(value: Decimal) -> str:
    """Render an exact Decimal as the frontend's canonical fixed-point string."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"0", "-0"} else rendered


def _on_control_lattice(
    value: int | Decimal, origin: int | Decimal, step: int | Decimal
) -> bool:
    (value_num, value_den), (origin_num, origin_den), (step_num, step_den) = (
        Decimal(item).as_integer_ratio() for item in (value, origin, step)
    )
    numerator = (value_num * origin_den - origin_num * value_den) * step_den
    return numerator % (value_den * origin_den * step_num) == 0


def validate_control_raw_value(
    controls: Mapping[str, object] | None, control: str, value: int
) -> tuple[int, int, int, int]:
    """Validate a raw control value against its published normalized domain.

    Returns the domain's ``(raw_min, raw_max, raw_step, raw_origin)`` when
    *value* is an integer inside ``[raw_min, raw_max]`` and on the
    ``raw_origin + k * raw_step`` lattice. Raises ``ValueError`` naming the
    control, its allowed range and its step otherwise, and when *controls*
    publishes no normalized scalar domain for *control*.
    """
    entry = controls.get(control) if controls is not None else None
    required = ("raw_min", "raw_max", "raw_step", "raw_origin")
    if not isinstance(entry, Mapping) or any(
        isinstance(entry.get(key), bool) or not isinstance(entry.get(key), int)
        for key in required
    ):
        raise ValueError(
            f"no normalized control domain for {control!r} in the active profile"
        )
    raw_min, raw_max, raw_step, raw_origin = (cast(int, entry[key]) for key in required)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not raw_min <= value <= raw_max
        or not _on_control_lattice(value, raw_origin, raw_step)
    ):
        raise ValueError(
            f"{control} must be within {raw_min}-{raw_max} on the "
            f"{raw_origin} + k*{raw_step} lattice; got {value!r}"
        )
    return raw_min, raw_max, raw_step, raw_origin


def _parse_decimal(value: object) -> _Decimal | None:
    """Parse a canonical decimal string into an exact (coefficient, scale)."""
    if (
        not isinstance(value, str)
        or value == "-0"
        or _CANONICAL_DECIMAL.match(value) is None
    ):
        return None
    negative = value.startswith("-")
    unsigned = value[1:] if negative else value
    integer, _, fraction = unsigned.partition(".")
    prefix = "-" if negative else ""
    return (int(f"{prefix}{integer}{fraction}"), len(fraction))


def _text(value: _Decimal) -> str:
    """Render through the same canonical rule as ``_public_decimal``."""
    coefficient, scale = value
    sign, magnitude = (1, -coefficient) if coefficient < 0 else (0, coefficient)
    digits = tuple(int(digit) for digit in str(magnitude))
    return _public_decimal(Decimal((sign, digits, -scale)))


def _align(values: tuple[_Decimal, ...]) -> tuple[int, ...]:
    scale = max(value[1] for value in values)
    return tuple(
        coefficient * 10 ** (scale - value_scale) for coefficient, value_scale in values
    )


def _compare(left: _Decimal, right: _Decimal) -> int:
    first, second = _align((left, right))
    return (first > second) - (first < second)


def _add(origin: _Decimal, steps: int, step: _Decimal) -> _Decimal:
    base, increment = _align((origin, step))
    return (base + steps * increment, max(origin[1], step[1]))


def _lattice(value: _Decimal, origin: _Decimal, step: _Decimal) -> int | None:
    if _compare(step, (0, 0)) <= 0:
        return None
    item, base, unit = _align((value, origin, step))
    quotient, remainder = divmod(item - base, unit)
    return quotient if remainder == 0 else None


def _safe_int(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and -_SAFE_INTEGER_MAX <= value <= _SAFE_INTEGER_MAX
    )


def _raw_index(domain: Mapping[str, object], raw: object) -> int | None:
    if not _safe_int(raw):
        return None
    bounds: list[int] = []
    for key in ("raw_min", "raw_max", "raw_step", "raw_origin"):
        item = domain.get(key)
        if not _safe_int(item):
            return None
        bounds.append(cast(int, item))
    raw_min, raw_max, raw_step, raw_origin = bounds
    if (
        raw_min >= raw_max
        or raw_step <= 0
        or not raw_min <= raw_origin <= raw_max
        or not raw_min <= cast(int, raw) <= raw_max
    ):
        return None
    if (raw_min - raw_origin) % raw_step != 0 or (raw_max - raw_origin) % raw_step != 0:
        return None
    lattice_value = cast(int, raw)
    if (lattice_value - raw_origin) % raw_step != 0:
        return None
    return (lattice_value - raw_origin) // raw_step


def _axis(domain: Mapping[str, object]) -> _Axis | None:
    values = [
        _parse_decimal(domain.get(key))
        for key in ("display_min", "display_max", "display_step", "display_origin")
    ]
    if any(value is None for value in values):
        return None
    result = cast(_Axis, tuple(values))
    if _compare(result[0], result[1]) >= 0 or _compare(result[2], (0, 0)) <= 0:
        return None
    if _compare(result[3], result[0]) < 0 or _compare(result[3], result[1]) > 0:
        return None
    if (
        _lattice(result[0], result[3], result[2]) is None
        or _lattice(result[1], result[3], result[2]) is None
    ):
        return None
    return result


def _in_axis(value: _Decimal, values: _Axis) -> bool:
    return (
        _compare(value, values[0]) >= 0
        and _compare(value, values[1]) <= 0
        and _lattice(value, values[3], values[2]) is not None
    )


def _in_range(value: _Decimal, values: _Axis) -> bool:
    return _compare(value, values[0]) >= 0 and _compare(value, values[1]) <= 0


def _lookup_points(
    domain: Mapping[str, object], values: _Axis
) -> tuple[_LookupPoint, ...] | None:
    candidate = domain.get("lookup")
    if not isinstance(candidate, list) or not candidate:
        return None
    points: list[_LookupPoint] = []
    raw_direction = 0
    display_direction = 0
    for item in candidate:
        if not isinstance(item, Mapping) or set(item) != {"raw", "display"}:
            return None
        raw = item["raw"]
        display_text = item["display"]
        display = _parse_decimal(display_text)
        if (
            not _safe_int(raw)
            or display is None
            or _raw_index(domain, raw) is None
            or not _in_axis(display, values)
        ):
            return None
        if points:
            previous = points[-1]
            next_raw_direction = (raw > previous[0]) - (raw < previous[0])
            next_display_direction = _compare(display, previous[2])
            if (
                next_raw_direction == 0
                or next_display_direction == 0
                or (raw_direction and raw_direction != next_raw_direction)
                or (display_direction and display_direction != next_display_direction)
            ):
                return None
            raw_direction = next_raw_direction
            display_direction = next_display_direction
        points.append((cast(int, raw), cast(str, display_text), display))
    return tuple(points)


def _scalar_index(domain: Mapping[str, object]) -> tuple[int, int] | None:
    raw_min = _raw_index(domain, domain.get("raw_min"))
    raw_origin = _raw_index(domain, domain.get("raw_origin"))
    if raw_min is None or raw_origin is None:
        return None
    return (raw_min, raw_origin)


def _same_cardinality(domain: Mapping[str, object], values: _Axis) -> bool:
    raw_min = _raw_index(domain, domain.get("raw_min"))
    raw_max = _raw_index(domain, domain.get("raw_max"))
    display_min = _lattice(values[0], values[3], values[2])
    display_max = _lattice(values[1], values[3], values[2])
    return (
        raw_min is not None
        and raw_max is not None
        and display_min is not None
        and display_max is not None
        and raw_max - raw_min == display_max - display_min
    )


def _is_identity(domain: Mapping[str, object], values: _Axis) -> bool:
    if (
        _raw_index(domain, domain.get("raw_min")) is None
        or _raw_index(domain, domain.get("raw_max")) is None
    ):
        return False
    for key, display in zip(("raw_min", "raw_max", "raw_step", "raw_origin"), values):
        raw = domain.get(key)
        if not _safe_int(raw) or _compare((cast(int, raw), 0), display) != 0:
            return False
    return True


def _centered_aligned(domain: Mapping[str, object], values: _Axis) -> bool:
    if domain.get("mapping") != "centered":
        return False
    raw_min = _raw_index(domain, domain.get("raw_min"))
    raw_center = _raw_index(domain, domain.get("raw_center"))
    display_center = _parse_decimal(domain.get("display_center"))
    display_offset = (
        _lattice(display_center, values[0], values[2])
        if display_center is not None
        else None
    )
    return (
        raw_min is not None
        and raw_center is not None
        and display_center is not None
        and _in_axis(display_center, values)
        and display_offset is not None
        and raw_center - raw_min == display_offset
        and _same_cardinality(domain, values)
    )


def _valid_domain(domain: Mapping[str, object], values: _Axis) -> bool:
    quantization = domain.get("quantization")
    if not isinstance(quantization, str) or quantization not in _QUANTIZATIONS:
        return False
    mapping = domain.get("mapping")
    if mapping == "identity":
        return _is_identity(domain, values)
    if mapping == "linear":
        indices = _scalar_index(domain)
        origin_index = _lattice(values[3], values[0], values[2])
        return (
            indices is not None
            and origin_index is not None
            and indices[1] - indices[0] == origin_index
            and _same_cardinality(domain, values)
        )
    if mapping == "centered":
        return _centered_aligned(domain, values)
    return mapping == "lookup" and _lookup_points(domain, values) is not None


def _domain_is_exact(domain: Mapping[str, object], values: _Axis) -> bool:
    if domain.get("restoration") != "exact":
        return False
    if domain.get("mapping") != "lookup":
        return True
    min_index = _raw_index(domain, domain.get("raw_min"))
    max_index = _raw_index(domain, domain.get("raw_max"))
    points = _lookup_points(domain, values)
    return (
        min_index is not None
        and max_index is not None
        and points is not None
        and len(points) == max_index - min_index + 1
    )


def decode_control_domain(domain: Mapping[str, object], raw: int) -> str | None:
    """Return the canonical display value for *raw*, or ``None`` when unusable."""
    if not isinstance(domain, Mapping):
        return None
    values = _axis(domain)
    index = _raw_index(domain, raw)
    if values is None or index is None or not _valid_domain(domain, values):
        return None
    mapping = domain.get("mapping")
    if mapping == "identity":
        return _text((raw, 0)) if _is_identity(domain, values) else None
    if mapping == "lookup":
        points = _lookup_points(domain, values)
        if points is None:
            return None
        for point_raw, display, _ in points:
            if point_raw == raw:
                return display
        return None
    if mapping == "linear":
        result = _add(values[3], index, values[2])
        return _text(result) if _in_axis(result, values) else None
    if mapping == "centered":
        center = _raw_index(domain, domain.get("raw_center"))
        display_center = _parse_decimal(domain.get("display_center"))
        if center is None or display_center is None:
            return None
        result = _add(display_center, index - center, values[2])
        return _text(result) if _in_axis(result, values) else None
    return None


def quantize_control_domain(domain: Mapping[str, object], display: str) -> str | None:
    """Apply the domain's display lattice posture without numeric coercion."""
    if not isinstance(domain, Mapping):
        return None
    values = _axis(domain)
    value = _parse_decimal(display)
    if (
        values is None
        or value is None
        or not _in_range(value, values)
        or not _valid_domain(domain, values)
    ):
        return None
    item, origin, step = _align((value, values[3], values[2]))
    quotient, remainder = divmod(item - origin, step)
    quantization = domain.get("quantization")
    if quantization == "floor":
        index = quotient
    elif quantization == "ceil":
        index = quotient if remainder == 0 else quotient + 1
    elif quantization == "reject":
        if remainder != 0:
            return None
        index = quotient
    elif quantization == "nearest_ties_down":
        index = quotient + 1 if remainder * 2 > step else quotient
    elif quantization == "nearest_ties_up":
        index = quotient + 1 if remainder * 2 >= step else quotient
    else:
        return None
    result = _add(values[3], index, values[2])
    return _text(result) if _in_axis(result, values) else None


def snap_control_domain(domain: Mapping[str, object], display: str) -> str | None:
    """Return the nearest legal display value to *display* (ties up).

    Snaps *display* onto the domain's display lattice with
    ``nearest_ties_up`` regardless of its published quantization,
    reusing :func:`quantize_control_domain`. Returns ``None`` when the
    domain is invalid or *display* is not a canonical decimal string;
    raises ``ValueError`` naming the display range when *display* is
    canonical but outside ``[display_min, display_max]``.
    """
    if not isinstance(domain, Mapping):
        return None
    values = _axis(domain)
    value = _parse_decimal(display)
    if values is None or value is None:
        return None
    if not _in_range(value, values):
        raise ValueError(
            f"display value {display!r} is outside the display range "
            f"{domain.get('display_min')}-{domain.get('display_max')}"
        )
    return quantize_control_domain(
        {**domain, "quantization": "nearest_ties_up"}, display
    )


def encode_control_domain(domain: Mapping[str, object], display: str) -> int | None:
    """Invert an exactly restorable domain; unavailable restoration returns ``None``."""
    if not isinstance(domain, Mapping):
        return None
    values = _axis(domain)
    if (
        values is None
        or not _valid_domain(domain, values)
        or not _domain_is_exact(domain, values)
    ):
        return None
    quantized = quantize_control_domain(domain, display)
    if quantized is None:
        return None
    mapping = domain.get("mapping")
    if mapping == "identity":
        if not _is_identity(domain, values):
            return None
        value = _parse_decimal(quantized)
        if (
            value is None
            or value[1] != 0
            or not cast(int, domain["raw_min"])
            <= value[0]
            <= cast(int, domain["raw_max"])
        ):
            return None
        raw = value[0]
        return raw if _raw_index(domain, raw) is not None else None
    if mapping == "lookup":
        points = _lookup_points(domain, values)
        if points is None:
            return None
        matches = [point[0] for point in points if point[1] == quantized]
        return matches[0] if len(matches) == 1 else None
    target = _parse_decimal(quantized)
    if target is None:
        return None
    if mapping == "linear":
        display_index = _lattice(target, values[3], values[2])
        if display_index is None:
            return None
        raw = cast(int, domain["raw_origin"]) + display_index * cast(
            int, domain["raw_step"]
        )
    elif mapping == "centered":
        center = _raw_index(domain, domain.get("raw_center"))
        display_center = _parse_decimal(domain.get("display_center"))
        if center is None or display_center is None:
            return None
        offset = _lattice(target, display_center, values[2])
        if offset is None:
            return None
        raw = cast(int, domain["raw_center"]) + offset * cast(int, domain["raw_step"])
    else:
        return None
    if not -_SAFE_INTEGER_MAX <= raw <= _SAFE_INTEGER_MAX:
        return None
    if (
        _raw_index(domain, raw) is None
        or decode_control_domain(domain, raw) != quantized
    ):
        return None
    return raw
