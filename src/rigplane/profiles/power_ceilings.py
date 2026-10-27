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

- a known band (the frequency the ceiling-defining observation resolves,
  inside a declared band that carries a row) or a known source names its own
  row;
- anything unknown — no frequency observed yet, a frequency no declared band
  covers, a source the state has not observed — resolves to the LOWEST
  declared row of that table, never the widest: an over-rated write is the
  failure this mechanism exists to prevent;
- band and source tables combine as their minimum;
- a profile declaring neither table resolves ``None`` and every caller keeps
  today's flat-``max_watts`` behaviour.

Which frequency defines the band: the canonical ``global.tx_state.tx_target``
authority when it names a KNOWN transmitting receiver with a frequency (the
band that actually transmits, also during split / cross-band operation), else
the active receiver's frequency. A tx_target that is OBSERVED-but-unknown
does not fall back to the selected receiver — the authority exists and says
it cannot name the transmitter, so no band is known and the lowest declared
ceiling applies; only a radio that never observes tx_target at all (no such
field in the snapshot, e.g. a single-receiver profile) uses the selected
receiver. Fields the freshness driver has aged to STALE are treated as
unobserved for the same reason. Both are read from the canonical StateStore
snapshot when the radio carries one — with a store attached it is the only
authority (production observation polling feeds it; the compatibility mirror
may lag or survive a provider reset) — falling back to the mirror only for
store-less radios. No shipped observation populates the power source yet, so
callers pass ``power_source=None`` and the lower source ceiling applies until
one does.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.state_pipeline_contracts import FieldFamily, FieldScope, VfoSlot
from ..core.state_store import FreshnessState

__all__ = [
    "active_receiver_freq_hz",
    "band_frequency_hz_from_snapshot",
    "effective_power_max_watts",
    "observed_band_freq_hz",
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


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _known_tx_target_freq_hz(value: Any) -> int | None:
    """The frequency of a KNOWN ``global.tx_state.tx_target`` value.

    Accepts the ``KnownTxTarget`` dataclass or its ``to_dict`` form
    (``{"status": "known", ..., "frequencyHz": ...}``). An unknown target,
    a target without a frequency, or any other shape resolves ``None`` —
    the caller then falls back to the active receiver's band.
    """
    if isinstance(value, Mapping):
        if value.get("status") != "known":
            return None
        return _positive_int(value.get("frequencyHz"))
    status = getattr(value, "status", None)
    if status != "known":
        return None
    return _positive_int(getattr(value, "frequency_hz", None))


def band_frequency_hz_from_snapshot(snapshot: Any) -> int | None:
    """The band-defining frequency of one canonical state snapshot.

    Precedence: a KNOWN ``global.tx_state.tx_target`` frequency (the band
    that transmits), else the ACTIVE receiver's ``freq_mode.freq_hz``. A
    tx_target that is present but unknown (or without a frequency)
    resolves ``None`` — the transmitter cannot be named, so no band is
    known; a snapshot with no tx_target field at all (a radio that never
    observes one) uses the selected receiver. STALE fields count as
    unobserved. Receiver-id aliases (``"0"``/``"main"``, ``"1"``/``"sub"``)
    collapse onto the same receiver. ``None`` when nothing qualifies —
    the caller then applies the lowest declared ceiling.
    """
    active = "MAIN"
    freqs: dict[str, int] = {}
    tx_freq: int | None = None
    tx_observed = False
    for field in getattr(snapshot, "fields", ()) or ():
        if getattr(field, "freshness", None) is FreshnessState.STALE:
            continue
        path = getattr(field, "path", None)
        if path is None:
            continue
        if (
            path.scope is FieldScope.GLOBAL
            and path.family is FieldFamily.TX_STATE
            and path.name == "tx_target"
        ):
            tx_observed = True
            tx_freq = _known_tx_target_freq_hz(getattr(field, "value", None))
            continue
        if (
            path.scope is FieldScope.GLOBAL
            and path.family is FieldFamily.SLOW_STATE
            and path.name == "active"
            and getattr(field, "value", None) in ("MAIN", "SUB")
        ):
            active = field.value
            continue
        if (
            path.scope is FieldScope.RECEIVER
            and path.family is FieldFamily.FREQ_MODE
            and path.name == "freq_hz"
            and path.slot in (None, VfoSlot.ACTIVE)
        ):
            freq_hz = _positive_int(getattr(field, "value", None))
            receiver_id = str(getattr(path, "receiver_id", ""))
            receiver_key = "sub" if receiver_id in ("1", "sub") else "main"
            if freq_hz is not None:
                freqs[receiver_key] = freq_hz
    if tx_observed:
        return tx_freq
    return freqs.get("sub" if active == "SUB" else "main")


def active_receiver_freq_hz(radio: Any) -> int | None:
    """The active receiver's frequency from the compatibility mirror.

    Reads the backend-neutral ``radio.radio_state`` (``RadioState``:
    ``main``/``sub`` receivers plus the ``active`` selector). A missing
    attribute, a non-``RadioState`` object or a non-positive frequency
    resolves ``None`` — the caller then applies the lowest declared
    ceiling. Production observation polling does not feed this mirror on
    every backend; prefer :func:`observed_band_freq_hz`.
    """
    state = getattr(radio, "radio_state", None)
    receiver = (
        getattr(state, "sub", None)
        if getattr(state, "active", None) == "SUB"
        else getattr(state, "main", None)
    )
    return _positive_int(getattr(receiver, "freq", None))


def observed_band_freq_hz(radio: Any) -> int | None:
    """The band-defining frequency from the radio's canonical observations.

    The canonical StateStore the web layer attaches as ``radio._state_store``
    (the only thing production observation polling feeds) is the sole
    authority when attached: its answer stands even when ``None`` (an empty
    or provider-reset store must apply the unknown-band ceiling, not
    resurrect the compatibility mirror, which may hold the previous
    connection's frequency). Store-less radios (direct backend use) fall
    back to the ``radio_state`` mirror, which ``set_freq`` keeps current.
    """
    store = getattr(radio, "_state_store", None)
    snapshot = getattr(store, "snapshot", None)
    if callable(snapshot):
        return band_frequency_hz_from_snapshot(snapshot())
    return active_receiver_freq_hz(radio)
