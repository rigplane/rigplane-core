"""Shared state polling/cache helpers used by web and rigctld.

This module centralises:
- Default cache TTLs for short-lived radio state (freq/mode/data_mode).
- A tiny helper for checking :class:`rigctld.state_cache.StateCache`
  freshness so callers do not duplicate the ``is_fresh`` logic and
  magic numbers.
- Async helpers that combine cache-freshness checks with radio polling
  so web and rigctld code share the same "serve-from-cache, poll if
  stale" pattern.

The goal is that both the Web UI and rigctld use the same TTL
semantics when deciding whether to serve cached values or hit the
radio again.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from icom_lan.core._state_cache import CacheField, StateCache

if TYPE_CHECKING:
    from icom_lan.radio_protocol import Radio

__all__ = [
    "DEFAULT_STATE_CACHE_TTL",
    "is_cache_fresh",
    "poll_frequency",
    "poll_mode",
    "poll_powerstat",
    "poll_standard_fields",
]

logger = logging.getLogger(__name__)

# Default TTL (seconds) for short-lived radio state such as frequency
# and mode.  Mirrors RigctldConfig.cache_ttl and is used by both
# rigctld and web snapshots unless a caller provides an override.
DEFAULT_STATE_CACHE_TTL: Final[float] = 0.2


def is_cache_fresh(
    cache: StateCache,
    field: CacheField,
    max_age_s: float | None,
) -> bool:
    """Return True if *field* in *cache* is fresh enough to use.

    Args:
        cache: Shared :class:`StateCache` instance.
        field: Cache field name (``"freq"``, ``"mode"``, etc.).
        max_age_s: Maximum acceptable age in seconds.  When ``None`` or
            non‑positive, the cache is treated as always stale and this
            function returns ``False``.
    """
    if max_age_s is None or max_age_s <= 0.0:
        return False
    return cache.is_fresh(field, max_age_s)


async def poll_frequency(
    radio: "Radio",
    cache: StateCache,
    ttl: float,
) -> int | None:
    """Return the current frequency, using cache when fresh.

    If the ``"freq"`` field in *cache* is younger than *ttl* seconds,
    the cached value is returned immediately without hitting the radio.
    Otherwise :meth:`Radio.get_freq` is called, the result stored
    in *cache*, and the fresh value returned.

    On any exception from the radio the cache is left unchanged and
    ``None`` is returned so callers can decide whether to serve a stale
    value or surface an error.

    Args:
        radio: Connected :class:`Radio` instance.
        cache: Shared :class:`StateCache` to read from / write to.
        ttl: Cache TTL in seconds.  Use ``0.0`` to always poll.

    Returns:
        Frequency in Hz, or ``None`` if the radio call failed.
    """
    if is_cache_fresh(cache, "freq", ttl):
        return cache.freq
    try:
        freq = await radio.get_freq()
        cache.update_freq(freq)
        return freq
    except Exception:
        logger.debug("poll_frequency: radio call failed", exc_info=True)
        return None


async def poll_mode(
    radio: "Radio",
    cache: StateCache,
    ttl: float,
    *,
    mode_reader: Callable[..., Awaitable[tuple[str, int | None]]] | None = None,
) -> tuple[str, int | None] | None:
    """Return the current mode, using cache when fresh.

    If the ``"mode"`` field in *cache* is younger than *ttl* seconds,
    ``(cache.mode, cache.filter_width)`` is returned immediately.
    Otherwise the radio is queried: if *mode_reader* is provided it is
    called (e.g. a rigctld-specific reader that normalises to hamlib
    strings); otherwise :meth:`Radio.get_mode` is used.  The result is
    stored in *cache* and returned.

    On any exception the cache is left unchanged and ``None`` is
    returned.

    Args:
        radio: Connected :class:`Radio` instance.
        cache: Shared :class:`StateCache` to read from / write to.
        ttl: Cache TTL in seconds.  Use ``0.0`` to always poll.
        mode_reader: Optional backend-specific callable that returns
            ``(mode_str, filter_width)``.  When ``None``,
            :meth:`Radio.get_mode` is used directly.

    Returns:
        ``(mode_str, filter_width)`` on success, ``None`` on failure.
    """
    if is_cache_fresh(cache, "mode", ttl):
        return (cache.mode, cache.filter_width)
    try:
        if mode_reader is not None:
            mode_str, filt = await mode_reader()
        else:
            mode_str, filt = await radio.get_mode()
        cache.update_mode(mode_str, filt)
        return (mode_str, filt)
    except Exception:
        logger.debug("poll_mode: radio call failed", exc_info=True)
        return None


async def poll_powerstat(
    radio: "Radio",
    cache: StateCache,
    ttl: float,
) -> bool | None:
    """Return the current power status, using cache when fresh.

    If the ``"powerstat"`` field in *cache* is younger than *ttl* seconds,
    the cached value is returned immediately without hitting the radio.
    Otherwise :meth:`Radio.get_powerstat` is called (if supported),
    the result stored in *cache*, and the fresh value returned.

    On any exception from the radio the cache is left unchanged and
    ``None`` is returned.

    Args:
        radio: Connected :class:`Radio` instance (must implement PowerControlCapable).
        cache: Shared :class:`StateCache` to read from / write to.
        ttl: Cache TTL in seconds.  Use ``0.0`` to always poll.

    Returns:
        True if powered on, False if powered off, or ``None`` if the radio call failed.
    """
    from icom_lan.capabilities import CAP_POWER_CONTROL

    if is_cache_fresh(cache, "powerstat", ttl):
        return cache.powerstat
    if CAP_POWER_CONTROL not in radio.capabilities:
        return None
    try:
        power_on = await radio.get_powerstat()  # type: ignore[attr-defined]
        cache.update_powerstat(power_on)
        return power_on  # type: ignore[no-any-return]
    except Exception:
        logger.debug("poll_powerstat: radio call failed", exc_info=True)
        return None


async def poll_standard_fields(
    radio: "Radio",
    cache: StateCache,
    ttl: float,
    *,
    mode_reader: Callable[..., Awaitable[tuple[str, int | None]]] | None = None,
) -> dict[str, object]:
    """Poll frequency, mode, and data-mode, using cache when fresh.

    Each field is fetched independently: a fresh cached value is served
    as-is; a stale field triggers a radio call.  Per-field failures are
    logged at DEBUG level and the corresponding key is omitted from the
    returned dict (or the cached value is used for ``data_mode``).

    Args:
        radio: Connected :class:`Radio` instance.
        cache: Shared :class:`StateCache` to read from / write to.
        ttl: Cache TTL in seconds.  Use ``0.0`` to always poll.
        mode_reader: Optional backend-specific mode reader passed
            through to :func:`poll_mode`.

    Returns:
        Dict with any subset of ``"freq"``, ``"mode"``,
        ``"filter_width"``, ``"data_mode"`` that were successfully
        obtained.
    """
    result: dict[str, object] = {}

    freq = await poll_frequency(radio, cache, ttl)
    if freq is not None:
        result["freq"] = freq

    mode_result = await poll_mode(radio, cache, ttl, mode_reader=mode_reader)
    if mode_result is not None:
        result["mode"] = mode_result[0]
        result["filter_width"] = mode_result[1]

    # data_mode — always include in result using cached value as fallback
    if not is_cache_fresh(cache, "data_mode", ttl):
        try:
            data_mode = await radio.get_data_mode()
            cache.update_data_mode(data_mode)
        except Exception:
            logger.debug("poll_standard_fields: get_data_mode failed", exc_info=True)
    result["data_mode"] = cache.data_mode

    return result
