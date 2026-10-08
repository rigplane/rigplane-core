"""Environment variable configuration helpers for rigplane.

Reads tunable parameters from the process environment so users on
high-latency or constrained links (VPN, cloud VMs) can adjust audio
and buffer behaviour without modifying code.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

__all__ = [
    "SdrEnvOverrides",
    "get_audio_sample_rate",
    "get_audio_broadcaster_high_watermark",
    "get_audio_rx_jitter_floor_ms",
    "get_audio_rx_jitter_ceiling_ms",
    "get_managed_tx_enabled",
    "get_sdr_env_overrides",
]

logger = logging.getLogger(__name__)

_SUPPORTED_SAMPLE_RATES = (8000, 16000, 24000, 48000)

_MANAGED_TX_VAR = "RIGPLANE_MANAGED_TX"
# The spellings the rest of the codebase already accepts: the truthy set of
# ``backends._icom_serial_base._env_bool`` and the falsy set the CLI reads
# ``ICOM_DEBUG`` against. Neither is extended here — a boolean knob that
# accepts a different vocabulary per variable is its own bug report.
_FALSY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "on", "yes"})

_DEFAULTS: dict[str, int] = {
    "ICOM_AUDIO_SAMPLE_RATE": 48000,
    "ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK": 10,
    "ICOM_AUDIO_RX_JITTER_FLOOR_MS": 50,
    "ICOM_AUDIO_RX_JITTER_CEILING_MS": 300,
}


def _read_positive_int(var: str) -> int:
    """Read *var* from the environment, validate it is a positive integer.

    Falls back to the default from ``_DEFAULTS`` and logs a warning when
    the value is absent, non-numeric, or not positive.
    """
    default = _DEFAULTS[var]
    raw = os.environ.get(var)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        msg = (
            f"env_config: {var}={raw!r} is not a valid integer, using default {default}"
        )
        logger.warning(msg)
        print(f"Warning: {msg}", file=sys.stderr)
        return default
    if value <= 0:
        msg = f"env_config: {var}={value} must be > 0, using default {default}"
        logger.warning(msg)
        print(f"Warning: {msg}", file=sys.stderr)
        return default
    return value


def _try_read_positive_int(var: str) -> tuple[int, bool]:
    """Read *var* and report parse success.

    Returns ``(value, ok)`` where ``ok`` is False ONLY when the env var is
    set but invalid (non-int or ``<= 0``). Absent vars return
    ``(default, True)``.
    """
    default = _DEFAULTS[var]
    raw = os.environ.get(var)
    if raw is None:
        return default, True
    try:
        value = int(raw)
    except ValueError:
        return default, False
    if value <= 0:
        return default, False
    return value, True


def get_audio_sample_rate() -> int:
    """Return the configured default audio sample rate in Hz.

    Reads ``ICOM_AUDIO_SAMPLE_RATE``.  The value must be one of the
    supported rates (8000, 16000, 24000, 48000).  Invalid values fall
    back to 48000 with a warning.
    """
    default = _DEFAULTS["ICOM_AUDIO_SAMPLE_RATE"]
    raw = os.environ.get("ICOM_AUDIO_SAMPLE_RATE")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        msg = (
            f"env_config: ICOM_AUDIO_SAMPLE_RATE={raw!r} is not a valid integer, "
            f"using default {default}"
        )
        logger.warning(msg)
        print(f"Warning: {msg}", file=sys.stderr)
        return default
    if value not in _SUPPORTED_SAMPLE_RATES:
        msg = (
            f"env_config: ICOM_AUDIO_SAMPLE_RATE={value} is not in supported rates "
            f"{_SUPPORTED_SAMPLE_RATES}, using default {default}"
        )
        logger.warning(msg)
        print(f"Warning: {msg}", file=sys.stderr)
        return default
    return value


def get_audio_broadcaster_high_watermark() -> int:
    """Return the configured broadcaster HIGH_WATERMARK.

    Reads ``ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK``.  Must be a positive integer.
    """
    return _read_positive_int("ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK")


def _jitter_bounds() -> tuple[int, int]:
    """Read and cross-validate jitter floor/ceiling.

    If either env var is set but invalid (non-int or ``<= 0``), BOTH revert
    to defaults. Then floor <= ceiling and ceiling <= 2000 are enforced; on
    any violation BOTH revert to defaults (no half-apply).
    """
    floor_default = _DEFAULTS["ICOM_AUDIO_RX_JITTER_FLOOR_MS"]
    ceiling_default = _DEFAULTS["ICOM_AUDIO_RX_JITTER_CEILING_MS"]
    floor, floor_ok = _try_read_positive_int("ICOM_AUDIO_RX_JITTER_FLOOR_MS")
    ceiling, ceiling_ok = _try_read_positive_int("ICOM_AUDIO_RX_JITTER_CEILING_MS")
    if not (floor_ok and ceiling_ok):
        invalid: list[str] = []
        if not floor_ok:
            invalid.append(
                f"ICOM_AUDIO_RX_JITTER_FLOOR_MS={os.environ.get('ICOM_AUDIO_RX_JITTER_FLOOR_MS')!r}"
            )
        if not ceiling_ok:
            invalid.append(
                f"ICOM_AUDIO_RX_JITTER_CEILING_MS={os.environ.get('ICOM_AUDIO_RX_JITTER_CEILING_MS')!r}"
            )
        msg = (
            f"env_config: invalid jitter env var(s) {', '.join(invalid)}, "
            f"reverting both to defaults ({floor_default}/{ceiling_default})"
        )
        logger.warning(msg)
        print(f"Warning: {msg}", file=sys.stderr)
        return floor_default, ceiling_default
    if ceiling > 2000:
        msg = (
            f"env_config: ICOM_AUDIO_RX_JITTER_CEILING_MS={ceiling} must be <= 2000, "
            f"reverting both to defaults ({floor_default}/{ceiling_default})"
        )
        logger.warning(msg)
        print(f"Warning: {msg}", file=sys.stderr)
        return floor_default, ceiling_default
    if floor > ceiling:
        msg = (
            f"env_config: ICOM_AUDIO_RX_JITTER_FLOOR_MS={floor} > "
            f"ICOM_AUDIO_RX_JITTER_CEILING_MS={ceiling}, "
            f"reverting both to defaults ({floor_default}/{ceiling_default})"
        )
        logger.warning(msg)
        print(f"Warning: {msg}", file=sys.stderr)
        return floor_default, ceiling_default
    return floor, ceiling


def get_audio_rx_jitter_floor_ms() -> int:
    """Return the configured RX jitter buffer floor in milliseconds.

    Reads ``ICOM_AUDIO_RX_JITTER_FLOOR_MS``.  Must be a positive integer
    and must not exceed the ceiling.  Falls back to 50 with a warning on
    any violation.
    """
    return _jitter_bounds()[0]


def get_audio_rx_jitter_ceiling_ms() -> int:
    """Return the configured RX jitter buffer ceiling in milliseconds.

    Reads ``ICOM_AUDIO_RX_JITTER_CEILING_MS``.  Must be a positive integer,
    must not exceed 2000, and must not be less than the floor.  Falls back
    to 300 with a warning on any violation.
    """
    return _jitter_bounds()[1]


def get_managed_tx_enabled() -> bool:
    """Return whether managed TX assembly is enabled.  Default: ``True``.

    Reads ``RIGPLANE_MANAGED_TX``.  This is an opt-**out**, not a feature
    flag: absent, empty, or any truthy spelling (``1``/``true``/``on``/``yes``)
    leaves managed TX on, and only an explicit ``0``/``false``/``off``/``no``
    turns it off.  Unrecognised values keep the default and warn, like the
    numeric knobs above — for a switch whose off position removes the
    supervisor from every key, "I could not read this" must resolve towards
    supervision rather than away from it.

    Off returns TX to the legacy, unsupervised ``set_ptt`` write: no lease, no
    owner, no keep-alive watchdog, no bounded key-down.  It is a field escape
    hatch for an operator whose rig cannot be supervised at all, which is why
    ``CoreRadio`` logs at WARNING on *every* connect while it is set rather
    than announcing it once at startup.

    Naming trap: the CLI's ``--managed`` flag (``args.managed_runtime``) is the
    local station runtime and has nothing to do with managed TX.  This
    variable is the only managed-TX switch.

    Read at arm time, never cached at import: one process may connect several
    radios, and the value that matters is the one in force when a radio arms.
    """
    raw = os.environ.get(_MANAGED_TX_VAR)
    if raw is None:
        return True
    value = raw.strip().lower()
    if value in _FALSY:
        return False
    if not value or value in _TRUTHY:
        return True
    msg = (
        f"env_config: {_MANAGED_TX_VAR}={raw!r} is not a recognised boolean, "
        "keeping managed TX enabled"
    )
    logger.warning(msg)
    print(f"Warning: {msg}", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# SDR panadapter env overrides (MOR-3201)
# ---------------------------------------------------------------------------

_SCOPE_SOURCE_VAR = "RIGPLANE_SCOPE_SOURCE"
_SDR_DEVICE_VAR = "RIGPLANE_SDR_DEVICE"
_SDR_SAMPLE_RATE_VAR = "RIGPLANE_SDR_SAMPLE_RATE"
_SDR_GAIN_VAR = "RIGPLANE_SDR_GAIN"
_SDR_PPM_VAR = "RIGPLANE_SDR_PPM"
_SDR_OFFSET_VAR = "RIGPLANE_SDR_OFFSET_HZ"
_SDR_SPAN_VAR = "RIGPLANE_SDR_SPAN_HZ"
_SDR_INVERT_VAR = "RIGPLANE_SDR_INVERT"
_SDR_SETTINGS_VAR = "RIGPLANE_SDR_SETTINGS"

_SCOPE_SOURCES = frozenset({"auto", "hardware", "sdr", "audio_fft"})


@dataclass(frozen=True)
class SdrEnvOverrides:
    """``RIGPLANE_SDR_*`` values set in the environment; ``None`` slot =
    variable unset, so the CLI flag's own value (or default) applies.
    The CLI merges these under its flags — a given flag wins — through
    ``sdr.runtime.resolve_sdr_config``."""

    scope_source: str | None = None
    device_args: str | None = None
    sample_rate_hz: int | None = None
    gain: str | None = None
    ppm: float | None = None
    freq_offset_hz: int | None = None
    span_hz: int | None = None
    invert_spectrum: bool | None = None
    settings: tuple[str, ...] | None = None


def _sdr_error(var: str, raw: str, expected: str) -> ValueError:
    return ValueError(f"{var}={raw!r} {expected}")


def _read_optional_str(var: str) -> str | None:
    """Read *var* as a non-empty string; unset or blank counts as unset."""
    raw = os.environ.get(var)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _read_sdr_int(var: str) -> int | None:
    raw = _read_optional_str(var)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise _sdr_error(var, raw, "is not a valid integer") from None


def _read_sdr_float(var: str) -> float | None:
    raw = _read_optional_str(var)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise _sdr_error(var, raw, "is not a valid number") from None


def get_sdr_env_overrides() -> SdrEnvOverrides:
    """Read the ``RIGPLANE_SDR_*`` / ``RIGPLANE_SCOPE_SOURCE`` overrides.

    Unlike the warn-and-fallback audio knobs above, a set-but-invalid
    value raises :class:`ValueError` naming the variable: an SDR is an
    explicit opt-in whose settings must reach the device verbatim.
    Cross-field rules (span ≤ 0.6 × sample rate) stay in
    ``sdr.runtime.resolve_sdr_config``.
    """
    scope_source = _read_optional_str(_SCOPE_SOURCE_VAR)
    if scope_source is not None and scope_source not in _SCOPE_SOURCES:
        raise _sdr_error(
            _SCOPE_SOURCE_VAR, scope_source, "must be auto/hardware/sdr/audio_fft"
        )

    gain = _read_optional_str(_SDR_GAIN_VAR)
    if gain is not None and gain.strip().lower() != "auto":
        try:
            float(gain)
        except ValueError:
            raise _sdr_error(
                _SDR_GAIN_VAR, gain, "is neither a number nor 'auto'"
            ) from None

    invert: bool | None = None
    invert_raw = _read_optional_str(_SDR_INVERT_VAR)
    if invert_raw is not None:
        lowered = invert_raw.lower()
        if lowered in _TRUTHY:
            invert = True
        elif lowered in _FALSY:
            invert = False
        else:
            raise _sdr_error(_SDR_INVERT_VAR, invert_raw, "is not a boolean")

    settings: tuple[str, ...] | None = None
    settings_raw = _read_optional_str(_SDR_SETTINGS_VAR)
    if settings_raw is not None:
        items = tuple(item.strip() for item in settings_raw.split(",") if item.strip())
        for item in items:
            key, sep, _ = item.partition("=")
            if not sep or not key:
                raise _sdr_error(_SDR_SETTINGS_VAR, item, "items must be KEY=VAL pairs")
        settings = items

    return SdrEnvOverrides(
        scope_source=scope_source,
        device_args=_read_optional_str(_SDR_DEVICE_VAR),
        sample_rate_hz=_read_sdr_int(_SDR_SAMPLE_RATE_VAR),
        gain=gain,
        ppm=_read_sdr_float(_SDR_PPM_VAR),
        freq_offset_hz=_read_sdr_int(_SDR_OFFSET_VAR),
        span_hz=_read_sdr_int(_SDR_SPAN_VAR),
        invert_spectrum=invert,
        settings=settings,
    )
