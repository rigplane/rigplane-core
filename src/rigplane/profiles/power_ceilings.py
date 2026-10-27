"""Rated-power ceilings resolved from profile data (MOR-3634).

Pure profile-data arithmetic in the spirit of ``profiles/control_domain.py``:
no backend, model name or ``if model == X`` branch lives here. A profile
declares per-band ceilings in ``[power.ceilings_w]`` (band name → watts, the
band names being the ``[[freq_ranges.ranges.bands]]`` entries) and per-source
ceilings in ``[power.sources]`` (source name → watts). The loader validates
both against ``[power].max_watts`` and the declared bands; this module turns
them into the single effective watt ceiling the existing power-limit seats
(``command_service.py: resolve_power_level_target`` inputs, the Yaesu
backend's ``set_power`` floor site, and the watt scale the UI projects)
consume.

Resolution rules, shared by band and source:

- a known band (the active receiver's frequency inside a declared band that
  carries a row) or a known source names its own row;
- anything unknown — no frequency observed yet, a frequency no declared band
  covers, a source the state has not observed — resolves to the LOWEST
  declared row of that table, never the widest: an over-rated write is the
  failure this mechanism exists to prevent;
- band and source tables combine as their minimum;
- a profile declaring neither table resolves ``None`` and every caller keeps
  today's flat-``max_watts`` behaviour.

No shipped observation populates the power source yet, so callers pass
``power_source=None`` and the lower source ceiling applies until one does.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "active_receiver_freq_hz",
    "effective_power_max_watts",
    "resolve_power_ceiling_watts",
]


def _watts_table(profile: Any, attr: str) -> dict[str, int] | None:
    table = getattr(profile, attr, None)
    if not isinstance(table, dict) or not table:
        return None
    return {
        name: watts
        for name, watts in table.items()
        if isinstance(watts, int) and not isinstance(watts, bool) and watts > 0
    }


def _band_ceiling_watts(
    profile: Any, ceilings: dict[str, int], freq_hz: int | None
) -> int:
    if isinstance(freq_hz, bool) or not isinstance(freq_hz, int) or freq_hz <= 0:
        return min(ceilings.values())
    matched: list[int] = []
    for freq_range in getattr(profile, "freq_ranges", ()) or ():
        for band in getattr(freq_range, "bands", ()) or ():
            name = getattr(band, "name", None)
            if name not in ceilings:
                continue
            start = getattr(band, "start", 0)
            end = getattr(band, "end", 0)
            if (
                isinstance(start, int)
                and isinstance(end, int)
                and start <= freq_hz <= end
            ):
                matched.append(ceilings[name])
    return min(matched) if matched else min(ceilings.values())


def resolve_power_ceiling_watts(
    profile: Any,
    *,
    freq_hz: int | None = None,
    power_source: str | None = None,
) -> int | None:
    """The rated-power ceiling in watts for the given band and source.

    ``None`` when the profile declares neither ``[power.ceilings_w]`` nor
    ``[power.sources]`` — today's flat-``max_watts`` behaviour. See the
    module docstring for the unknown-band/unknown-source rules.
    """
    ceilings = _watts_table(profile, "power_ceilings_w")
    sources = _watts_table(profile, "power_sources_w")
    if ceilings is None and sources is None:
        return None

    candidates: list[int] = []
    if ceilings is not None:
        candidates.append(_band_ceiling_watts(profile, ceilings, freq_hz))
    if sources is not None:
        if isinstance(power_source, str) and power_source in sources:
            candidates.append(sources[power_source])
        else:
            candidates.append(min(sources.values()))

    max_watts = getattr(profile, "max_watts", None)
    if isinstance(max_watts, int) and not isinstance(max_watts, bool):
        if max_watts > 0:
            candidates.append(max_watts)
    return min(candidates)


def effective_power_max_watts(
    profile: Any,
    *,
    freq_hz: int | None = None,
    power_source: str | None = None,
) -> int | float | None:
    """The watt scale a caller should use where it used ``profile.max_watts``.

    The resolved ceiling when the profile declares one, the flat
    ``max_watts`` otherwise (and ``None`` when the profile has neither).
    """
    ceiling = resolve_power_ceiling_watts(
        profile, freq_hz=freq_hz, power_source=power_source
    )
    if ceiling is not None:
        return ceiling
    max_watts = getattr(profile, "max_watts", None)
    if isinstance(max_watts, (int, float)) and not isinstance(max_watts, bool):
        return max_watts
    return None


def active_receiver_freq_hz(radio: Any) -> int | None:
    """The active receiver's frequency, or ``None`` while unobserved.

    Reads the backend-neutral ``radio.radio_state`` (``RadioState``:
    ``main``/``sub`` receivers plus the ``active`` selector). A missing
    attribute, a non-``RadioState`` object or a non-positive frequency
    resolves ``None`` — the caller then applies the lowest declared ceiling.
    """
    state = getattr(radio, "radio_state", None)
    receiver = (
        getattr(state, "sub", None)
        if getattr(state, "active", None) == "SUB"
        else getattr(state, "main", None)
    )
    freq_hz = getattr(receiver, "freq", None)
    if isinstance(freq_hz, int) and not isinstance(freq_hz, bool) and freq_hz > 0:
        return freq_hz
    return None
