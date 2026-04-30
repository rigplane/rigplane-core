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
]

logger = logging.getLogger(__name__)

_SUPPORTED_SAMPLE_RATES = (8000, 16000, 24000, 48000)

_DEFAULTS: dict[str, int] = {
    "ICOM_AUDIO_SAMPLE_RATE": 48000,
    "ICOM_AUDIO_BROADCASTER_HIGH_WATERMARK": 10,
    "ICOM_AUDIO_CLIENT_HIGH_WATERMARK": 10,
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
