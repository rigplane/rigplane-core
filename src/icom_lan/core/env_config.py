"""Environment variable configuration helpers for icom-lan.

Reads tunable parameters from the process environment so users on
high-latency or constrained links (VPN, cloud VMs) can adjust audio
and buffer behaviour without modifying code.
"""

import logging
import os
import sys

__all__ = [
    "get_audio_sample_rate",
    "get_audio_broadcaster_high_watermark",
    "get_audio_client_high_watermark",
    "get_audio_rx_jitter_floor_ms",
    "get_audio_rx_jitter_ceiling_ms",
]

logger = logging.getLogger(__name__)

_SUPPORTED_SAMPLE_RATES = (8000, 16000, 24000, 48000)

_DEFAULTS: dict[str, int] = {
    "ICOM_AUDIO_SAMPLE_RATE": 48000,
    "ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK": 10,
    "ICOM_AUDIO_CLIENT_HIGH_WATERMARK": 10,
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


def get_audio_client_high_watermark() -> int:
    """Return the configured per-client audio HIGH_WATERMARK.

    Reads ``ICOM_AUDIO_CLIENT_HIGH_WATERMARK``.  Must be a positive integer.
    """
    return _read_positive_int("ICOM_AUDIO_CLIENT_HIGH_WATERMARK")


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
